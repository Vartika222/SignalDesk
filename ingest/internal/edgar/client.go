// Package edgar is a polite client for SEC EDGAR: it identifies itself, stays under the fair-access
// request rate, and retries transient failures with backoff.
package edgar

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// MaxRequestsPerSecond is below the SEC's published ceiling of 10 req/s.
const MaxRequestsPerSecond = 8

// Client fetches EDGAR URLs. It is safe for concurrent use; the limiter is shared.
type Client struct {
	HTTP       *http.Client
	UserAgent  string
	MaxRetries int
	limiter    *limiter
	sleep      func(context.Context, time.Duration) error
}

// NewClient refuses an empty User-Agent: the SEC requires a declared contact.
func NewClient(userAgent string) (*Client, error) {
	ua := strings.TrimSpace(userAgent)
	if ua == "" || !strings.Contains(ua, "@") {
		return nil, errors.New("SEC_USER_AGENT must be set to \"Name contact@email\" (SEC fair-access policy)")
	}
	return &Client{
		HTTP:       &http.Client{Timeout: 60 * time.Second},
		UserAgent:  ua,
		MaxRetries: 5,
		limiter:    newLimiter(MaxRequestsPerSecond),
		sleep:      sleepCtx,
	}, nil
}

// StatusError is returned for non-retryable HTTP statuses.
type StatusError struct {
	URL    string
	Status int
}

func (e *StatusError) Error() string { return fmt.Sprintf("GET %s: HTTP %d", e.URL, e.Status) }

// Get fetches url, retrying 429, 5xx and network errors with exponential backoff
// (honouring Retry-After). Other statuses fail immediately.
func (c *Client) Get(ctx context.Context, url string) ([]byte, error) {
	var lastErr error
	var wait time.Duration
	for attempt := 0; attempt <= c.MaxRetries; attempt++ {
		if attempt > 0 {
			if err := c.sleep(ctx, wait); err != nil {
				return nil, err
			}
		}
		if err := c.limiter.wait(ctx); err != nil {
			return nil, err
		}
		res := c.do(ctx, url)
		if res.err == nil {
			return res.body, nil
		}
		if !res.retryable {
			return nil, res.err
		}
		lastErr = res.err
		wait = backoff(attempt+1, res.retryAfter)
	}
	return nil, fmt.Errorf("GET %s: giving up after %d attempts: %w", url, c.MaxRetries+1, lastErr)
}

type result struct {
	body       []byte
	err        error
	retryable  bool
	retryAfter time.Duration
}

func (c *Client) do(ctx context.Context, url string) result {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return result{err: err}
	}
	req.Header.Set("User-Agent", c.UserAgent)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return result{err: ctx.Err()}
		}
		return result{err: err, retryable: true}
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return result{err: err, retryable: true}
	}
	switch {
	case resp.StatusCode == http.StatusOK:
		return result{body: body}
	case resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode >= 500:
		r := result{err: &StatusError{URL: url, Status: resp.StatusCode}, retryable: true}
		if secs, err := strconv.Atoi(resp.Header.Get("Retry-After")); err == nil {
			r.retryAfter = time.Duration(secs) * time.Second
		}
		return r
	default:
		return result{err: &StatusError{URL: url, Status: resp.StatusCode}}
	}
}

func backoff(attempt int, retryAfter time.Duration) time.Duration {
	if retryAfter > 0 {
		return retryAfter
	}
	d := time.Duration(1<<uint(attempt-1)) * 500 * time.Millisecond // 0.5s, 1s, 2s, 4s, ...
	if d > 30*time.Second {
		d = 30 * time.Second
	}
	return d
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// limiter enforces a minimum interval between requests across goroutines.
type limiter struct {
	mu       sync.Mutex
	interval time.Duration
	next     time.Time
}

func newLimiter(perSecond int) *limiter {
	return &limiter{interval: time.Second / time.Duration(perSecond)}
}

func (l *limiter) wait(ctx context.Context) error {
	l.mu.Lock()
	now := time.Now()
	if l.next.Before(now) {
		l.next = now
	}
	slot := l.next
	l.next = l.next.Add(l.interval)
	l.mu.Unlock()
	if d := time.Until(slot); d > 0 {
		return sleepCtx(ctx, d)
	}
	return nil
}
