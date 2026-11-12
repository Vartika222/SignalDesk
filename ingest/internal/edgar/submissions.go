package edgar

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Filing is one row of the submissions feed.
type Filing struct {
	CIK                string
	AccessionNumber    string // 0000320193-24-000123
	Form               string
	Items              string // 8-K items, comma separated
	FilingDate         time.Time
	ReportDate         *time.Time
	AcceptanceDateTime *time.Time // as labelled in the JSON; audited against -index.htm
	PrimaryDocument    string
}

// AccessionNoDashes is the folder name used under /Archives.
func (f Filing) AccessionNoDashes() string { return strings.ReplaceAll(f.AccessionNumber, "-", "") }

// CIKInt is the CIK without zero padding, as used in Archive paths.
func (f Filing) CIKInt() string {
	n, err := strconv.Atoi(f.CIK)
	if err != nil {
		return strings.TrimLeft(f.CIK, "0")
	}
	return strconv.Itoa(n)
}

// IndexURL is the filing's human index page, which carries the authoritative "Accepted" time.
func (f Filing) IndexURL() string {
	return fmt.Sprintf("https://www.sec.gov/Archives/edgar/data/%s/%s/%s-index.htm",
		f.CIKInt(), f.AccessionNoDashes(), f.AccessionNumber)
}

// DocURL is the Archive URL of a file inside this filing.
func (f Filing) DocURL(name string) string {
	return fmt.Sprintf("https://www.sec.gov/Archives/edgar/data/%s/%s/%s",
		f.CIKInt(), f.AccessionNoDashes(), name)
}

// columnar is the shape of filings.recent and of each older page: parallel arrays.
type columnar struct {
	AccessionNumber    []string `json:"accessionNumber"`
	FilingDate         []string `json:"filingDate"`
	ReportDate         []string `json:"reportDate"`
	AcceptanceDateTime []string `json:"acceptanceDateTime"`
	Form               []string `json:"form"`
	Items              []string `json:"items"`
	PrimaryDocument    []string `json:"primaryDocument"`
}

// Submissions is the parsed CIK##########.json feed.
type Submissions struct {
	CIK    string
	Name   string
	Recent []Filing
	// Older pages of history (file names under data.sec.gov/submissions/).
	OlderPages []OlderPage
}

type OlderPage struct {
	Name       string `json:"name"`
	FilingFrom string `json:"filingFrom"`
	FilingTo   string `json:"filingTo"`
}

// ParseSubmissions decodes the main submissions JSON.
func ParseSubmissions(body []byte) (*Submissions, error) {
	var raw struct {
		CIK     string `json:"cik"`
		Name    string `json:"name"`
		Filings struct {
			Recent columnar    `json:"recent"`
			Files  []OlderPage `json:"files"`
		} `json:"filings"`
	}
	if err := json.Unmarshal(body, &raw); err != nil {
		return nil, fmt.Errorf("parse submissions: %w", err)
	}
	cik := fmt.Sprintf("%010s", raw.CIK)
	cik = strings.ReplaceAll(cik, " ", "0")
	filings, err := raw.Filings.Recent.toFilings(cik)
	if err != nil {
		return nil, err
	}
	return &Submissions{CIK: cik, Name: raw.Name, Recent: filings, OlderPages: raw.Filings.Files}, nil
}

// ParseOlderPage decodes one older-history page (same columnar shape, no wrapper).
func ParseOlderPage(cik string, body []byte) ([]Filing, error) {
	var c columnar
	if err := json.Unmarshal(body, &c); err != nil {
		return nil, fmt.Errorf("parse older page: %w", err)
	}
	return c.toFilings(cik)
}

func (c columnar) toFilings(cik string) ([]Filing, error) {
	n := len(c.AccessionNumber)
	for name, l := range map[string]int{
		"filingDate": len(c.FilingDate), "form": len(c.Form), "primaryDocument": len(c.PrimaryDocument),
	} {
		if l != n {
			return nil, fmt.Errorf("submissions: column %s has %d rows, want %d", name, l, n)
		}
	}
	out := make([]Filing, 0, n)
	for i := 0; i < n; i++ {
		fd, err := time.Parse("2006-01-02", c.FilingDate[i])
		if err != nil {
			return nil, fmt.Errorf("filingDate %q: %w", c.FilingDate[i], err)
		}
		f := Filing{
			CIK:             cik,
			AccessionNumber: c.AccessionNumber[i],
			Form:            c.Form[i],
			FilingDate:      fd,
			PrimaryDocument: c.PrimaryDocument[i],
		}
		if i < len(c.Items) {
			f.Items = c.Items[i]
		}
		if i < len(c.ReportDate) && c.ReportDate[i] != "" {
			if rd, err := time.Parse("2006-01-02", c.ReportDate[i]); err == nil {
				f.ReportDate = &rd
			}
		}
		if i < len(c.AcceptanceDateTime) && c.AcceptanceDateTime[i] != "" {
			if at, err := time.Parse(time.RFC3339, c.AcceptanceDateTime[i]); err == nil {
				f.AcceptanceDateTime = &at
			}
		}
		out = append(out, f)
	}
	return out, nil
}

// HasItem reports whether an 8-K lists the given item (e.g. "2.02").
func (f Filing) HasItem(item string) bool {
	for _, it := range strings.Split(f.Items, ",") {
		if strings.TrimSpace(it) == item {
			return true
		}
	}
	return false
}

// Selection says how many of the most recent filings of each form to take.
type Selection struct {
	PerForm map[string]int // exact form match; amendments (10-K/A) are excluded unless listed
	Since   time.Time      // zero = no lower bound
	// For 8-K, only earnings releases (Item 2.02) carry guidance language.
	Require8KItem string
}

// Select returns the newest matching filings per form, in feed order (newest first).
func (s Selection) Select(filings []Filing) []Filing {
	counts := map[string]int{}
	var out []Filing
	for _, f := range filings {
		want, ok := s.PerForm[f.Form]
		if !ok || (want > 0 && counts[f.Form] >= want) {
			continue
		}
		if !s.Since.IsZero() && f.FilingDate.Before(s.Since) {
			continue
		}
		if f.Form == "8-K" && s.Require8KItem != "" && !f.HasItem(s.Require8KItem) {
			continue
		}
		counts[f.Form]++
		out = append(out, f)
	}
	return out
}

// ParseSelection parses "10-K:1,10-Q:2,8-K:2" (0 means unlimited).
func ParseSelection(spec string) (map[string]int, error) {
	out := map[string]int{}
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		form, n, ok := strings.Cut(part, ":")
		if !ok {
			return nil, fmt.Errorf("bad form spec %q (want FORM:N)", part)
		}
		k, err := strconv.Atoi(n)
		if err != nil || k < 0 {
			return nil, fmt.Errorf("bad count in %q", part)
		}
		out[strings.TrimSpace(form)] = k
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("empty form spec")
	}
	return out, nil
}
