// Command ingest fetches SEC filings for a list of tickers and stores the raw documents with their
// acceptance time. It is idempotent: re-running skips documents already stored.
//
//	SEC_USER_AGENT="Your Name you@example.com" go run ./cmd/ingest -tickers AAPL,MSFT -forms 10-K:1,10-Q:2,8-K:2
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"time"

	"signaldesk/ingest/internal/edgar"
	"signaldesk/ingest/internal/store"
)

type config struct {
	tickers  []string
	forms    map[string]int
	since    time.Time
	dataDir  string
	dbURL    string
	ua       string
	dryRun   bool
	maxPages int
}

func main() {
	cfg, err := parseFlags()
	if err != nil {
		fmt.Fprintln(os.Stderr, "ingest:", err)
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	if err := run(ctx, cfg); err != nil {
		slog.Error("ingest failed", "err", err)
		os.Exit(1)
	}
}

func parseFlags() (config, error) {
	var cfg config
	tickers := flag.String("tickers", "", "comma-separated tickers")
	tickersFile := flag.String("tickers-file", "", "file with one ticker per line (# comments ok)")
	forms := flag.String("forms", "10-K:1,10-Q:2,8-K:2", "FORM:N pairs, newest N of each (0 = all)")
	since := flag.String("since", "", "only filings on or after YYYY-MM-DD (fetches older history pages)")
	flag.StringVar(&cfg.dataDir, "data-dir", envOr("DATA_DIR", "data"), "raw file directory")
	flag.StringVar(&cfg.dbURL, "db", os.Getenv("DATABASE_URL"), "Postgres URL")
	flag.BoolVar(&cfg.dryRun, "dry-run", false, "list selected filings without downloading documents")
	flag.IntVar(&cfg.maxPages, "max-older-pages", 20, "cap on older submissions pages per company")
	flag.Parse()

	cfg.ua = os.Getenv("SEC_USER_AGENT")
	for _, t := range strings.Split(*tickers, ",") {
		if t = strings.ToUpper(strings.TrimSpace(t)); t != "" {
			cfg.tickers = append(cfg.tickers, t)
		}
	}
	if *tickersFile != "" {
		b, err := os.ReadFile(*tickersFile)
		if err != nil {
			return cfg, err
		}
		for _, line := range strings.Split(string(b), "\n") {
			line, _, _ = strings.Cut(line, "#")
			if t := strings.ToUpper(strings.TrimSpace(line)); t != "" {
				cfg.tickers = append(cfg.tickers, t)
			}
		}
	}
	if len(cfg.tickers) == 0 {
		return cfg, fmt.Errorf("no tickers (use -tickers or -tickers-file)")
	}
	f, err := edgar.ParseSelection(*forms)
	if err != nil {
		return cfg, err
	}
	cfg.forms = f
	if *since != "" {
		if cfg.since, err = time.Parse("2006-01-02", *since); err != nil {
			return cfg, fmt.Errorf("-since: %w", err)
		}
	}
	if cfg.dbURL == "" && !cfg.dryRun {
		return cfg, fmt.Errorf("DATABASE_URL or -db is required")
	}
	return cfg, nil
}

func run(ctx context.Context, cfg config) error {
	client, err := edgar.NewClient(cfg.ua)
	if err != nil {
		return err
	}
	var st *store.Store
	if !cfg.dryRun {
		if st, err = store.Open(ctx, cfg.dbURL); err != nil {
			return fmt.Errorf("open db: %w", err)
		}
		defer st.Close()
	}

	tickerMap, err := loadTickerMap(ctx, client, cfg.dataDir)
	if err != nil {
		return err
	}
	sel := edgar.Selection{PerForm: cfg.forms, Since: cfg.since, Require8KItem: "2.02"}

	var stats struct{ filings, stored, skipped, failed int }
	for _, ticker := range cfg.tickers {
		co, ok := tickerMap[ticker]
		if !ok {
			slog.Warn("unknown ticker", "ticker", ticker)
			stats.failed++
			continue
		}
		filings, name, err := companyFilings(ctx, client, cfg, co.CIK)
		if err != nil {
			slog.Error("submissions", "ticker", ticker, "err", err)
			stats.failed++
			continue
		}
		chosen := sel.Select(filings)
		slog.Info("company", "ticker", ticker, "cik", co.CIK, "available", len(filings), "selected", len(chosen))
		if cfg.dryRun {
			for _, f := range chosen {
				fmt.Printf("%s\t%s\t%s\t%s\t%s\n", ticker, f.Form, f.FilingDate.Format("2006-01-02"), f.AccessionNumber, f.Items)
			}
			continue
		}
		companyID, err := st.UpsertCompany(ctx, co.CIK, ticker, name)
		if err != nil {
			return err
		}
		for _, f := range chosen {
			stats.filings++
			n, err := ingestFiling(ctx, client, st, cfg.dataDir, companyID, f)
			if err != nil {
				slog.Error("filing", "ticker", ticker, "accession", f.AccessionNumber, "err", err)
				stats.failed++
				continue
			}
			if n == 0 {
				stats.skipped++
			}
			stats.stored += n
		}
	}
	slog.Info("done", "filings", stats.filings, "documents_stored", stats.stored,
		"already_present", stats.skipped, "failures", stats.failed)
	if stats.failed > 0 {
		return fmt.Errorf("%d failures (see log)", stats.failed)
	}
	return nil
}

type tickerEntry struct {
	CIK   string
	Title string
}

// loadTickerMap fetches company_tickers.json once and caches it under data/raw.
func loadTickerMap(ctx context.Context, c *edgar.Client, dataDir string) (map[string]tickerEntry, error) {
	path := filepath.Join(dataDir, "raw", "company_tickers.json")
	body, err := cachedGet(ctx, c, "https://www.sec.gov/files/company_tickers.json", path, 24*time.Hour)
	if err != nil {
		return nil, err
	}
	var raw map[string]struct {
		CIK    int    `json:"cik_str"`
		Ticker string `json:"ticker"`
		Title  string `json:"title"`
	}
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, fmt.Errorf("company_tickers.json: %w", err)
	}
	out := make(map[string]tickerEntry, len(raw))
	for _, r := range raw {
		out[strings.ToUpper(r.Ticker)] = tickerEntry{CIK: fmt.Sprintf("%010d", r.CIK), Title: r.Title}
	}
	return out, nil
}

// companyFilings returns recent filings plus older pages when -since reaches back past them.
func companyFilings(ctx context.Context, c *edgar.Client, cfg config, cik string) ([]edgar.Filing, string, error) {
	dir := filepath.Join(cfg.dataDir, "raw", "submissions")
	body, err := cachedGet(ctx, c, "https://data.sec.gov/submissions/CIK"+cik+".json",
		filepath.Join(dir, "CIK"+cik+".json"), 12*time.Hour)
	if err != nil {
		return nil, "", err
	}
	sub, err := edgar.ParseSubmissions(body)
	if err != nil {
		return nil, "", err
	}
	filings := sub.Recent
	if !cfg.since.IsZero() {
		for i, p := range sub.OlderPages {
			if i >= cfg.maxPages {
				break
			}
			if to, err := time.Parse("2006-01-02", p.FilingTo); err == nil && to.Before(cfg.since) {
				continue
			}
			b, err := cachedGet(ctx, c, "https://data.sec.gov/submissions/"+p.Name, filepath.Join(dir, p.Name), 7*24*time.Hour)
			if err != nil {
				return nil, "", err
			}
			older, err := edgar.ParseOlderPage(sub.CIK, b)
			if err != nil {
				return nil, "", err
			}
			filings = append(filings, older...)
		}
	}
	return filings, sub.Name, nil
}

// ingestFiling fetches the filing's index page (for the authoritative acceptance time) and the
// document we analyse: the primary document for 10-K/10-Q, the EX-99.1 press release for 8-K.
func ingestFiling(ctx context.Context, c *edgar.Client, st *store.Store, dataDir string, companyID int64, f edgar.Filing) (int, error) {
	kind := "primary"
	if f.Form == "8-K" {
		kind = "ex99_1"
	}
	if have, err := st.HasDocument(ctx, f.AccessionNumber, kind); err != nil || have {
		return 0, err
	}
	folder := filepath.Join("raw", f.CIK, f.AccessionNoDashes())
	idxBody, err := cachedGet(ctx, c, f.IndexURL(), filepath.Join(dataDir, folder, "index.htm"), 0)
	if err != nil {
		return 0, err
	}
	idx, err := edgar.ParseIndex(idxBody)
	if err != nil {
		return 0, err
	}

	name := f.PrimaryDocument
	if kind == "ex99_1" {
		doc, ok := idx.FindExhibit("EX-99.1", "EX-99")
		if !ok {
			return 0, fmt.Errorf("8-K %s has no EX-99 exhibit", f.AccessionNumber)
		}
		name = doc.Name
	}
	if name == "" {
		return 0, fmt.Errorf("no document name for %s", f.AccessionNumber)
	}
	url := f.DocURL(name)
	rel := filepath.Join(folder, name)
	body, err := cachedGet(ctx, c, url, filepath.Join(dataDir, rel), 0)
	if err != nil {
		return 0, err
	}
	sum := sha256.Sum256(body)
	inserted, err := st.InsertDocument(ctx, store.Document{
		CompanyID:      companyID,
		AccessionNo:    f.AccessionNumber,
		Form:           f.Form,
		DocKind:        kind,
		Items:          f.Items,
		FileName:       name,
		SourceURL:      url,
		FiledDate:      f.FilingDate,
		PeriodOfReport: f.ReportDate,
		AcceptedAt:     idx.AcceptedAt,
		AcceptedAtJSON: f.AcceptanceDateTime,
		ContentHash:    hex.EncodeToString(sum[:]),
		RawPath:        rel,
		RawBytes:       int64(len(body)),
	})
	if err != nil || !inserted {
		return 0, err
	}
	return 1, nil
}

// cachedGet returns the file at path if it is younger than maxAge (0 = forever), else fetches it.
func cachedGet(ctx context.Context, c *edgar.Client, url, path string, maxAge time.Duration) ([]byte, error) {
	if info, err := os.Stat(path); err == nil && (maxAge == 0 || time.Since(info.ModTime()) < maxAge) {
		return os.ReadFile(path)
	}
	body, err := c.Get(ctx, url)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	tmp := path + ".part"
	if err := os.WriteFile(tmp, body, 0o644); err != nil {
		return nil, err
	}
	return body, os.Rename(tmp, path)
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
