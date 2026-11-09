// Package store writes companies and raw documents to Postgres.
package store

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type Store struct{ pool *pgxpool.Pool }

func Open(ctx context.Context, url string) (*Store, error) {
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close() { s.pool.Close() }

// UpsertCompany returns the company id for a CIK, creating or refreshing the row.
func (s *Store) UpsertCompany(ctx context.Context, cik, ticker, name string) (int64, error) {
	var id int64
	err := s.pool.QueryRow(ctx, `
		INSERT INTO companies (cik, ticker, name) VALUES ($1, $2, $3)
		ON CONFLICT (cik) DO UPDATE SET ticker = EXCLUDED.ticker, name = EXCLUDED.name
		RETURNING id`, cik, ticker, name).Scan(&id)
	return id, err
}

// HasDocument reports whether a document for this accession and kind is already stored.
func (s *Store) HasDocument(ctx context.Context, accession, kind string) (bool, error) {
	var exists bool
	err := s.pool.QueryRow(ctx,
		`SELECT EXISTS (SELECT 1 FROM documents WHERE accession_no = $1 AND doc_kind = $2)`,
		accession, kind).Scan(&exists)
	return exists, err
}

type Document struct {
	CompanyID      int64
	AccessionNo    string
	Form           string
	DocKind        string
	Items          string
	FileName       string
	SourceURL      string
	FiledDate      time.Time
	PeriodOfReport *time.Time
	AcceptedAt     time.Time
	AcceptedAtJSON *time.Time
	ContentHash    string
	RawPath        string
	RawBytes       int64
}

// InsertDocument stores a document. It returns false if an identical document (same accession and
// kind, or same content hash) already exists.
func (s *Store) InsertDocument(ctx context.Context, d Document) (bool, error) {
	tag, err := s.pool.Exec(ctx, `
		INSERT INTO documents (company_id, accession_no, form, doc_kind, items, file_name, source_url,
			filed_date, period_of_report, accepted_at, accepted_at_json, published_at,
			content_hash, raw_path, raw_bytes)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$10,$12,$13,$14)
		ON CONFLICT DO NOTHING`,
		d.CompanyID, d.AccessionNo, d.Form, d.DocKind, nullIfEmpty(d.Items), d.FileName, d.SourceURL,
		d.FiledDate, d.PeriodOfReport, d.AcceptedAt, d.AcceptedAtJSON,
		d.ContentHash, d.RawPath, d.RawBytes)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() == 1, nil
}

func nullIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}
