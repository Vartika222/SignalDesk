package edgar

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

const indexFixture = `<html><body>
<div class="formGrouping">
<div class="infoHead">Filing Date</div>
<div class="info">2024-10-31</div>
<div class="infoHead">Accepted</div>
<div class="info">2024-10-31 16:30:47</div>
</div>
<table class="tableFile" summary="Document Format Files">
<tr><th scope="col">Seq</th><th scope="col">Description</th><th scope="col">Document</th><th scope="col">Type</th><th scope="col">Size</th></tr>
<tr><td scope="row">1</td><td scope="row">8-K</td>
<td scope="row"><a href="/ix?doc=/Archives/edgar/data/320193/000032019324000120/aapl-20241031.htm">aapl-20241031.htm</a> <span>iXBRL</span></td>
<td scope="row">8-K</td><td scope="row">30000</td></tr>
<tr><td scope="row">2</td><td scope="row">PRESS RELEASE</td>
<td scope="row"><a href="/Archives/edgar/data/320193/000032019324000120/a8-kex991q4202409282024.htm">a8-kex991q4202409282024.htm</a></td>
<td scope="row">EX-99.1</td><td scope="row">120000</td></tr>
</table></body></html>`

func TestParseIndexAcceptedIsEasternConvertedToUTC(t *testing.T) {
	p, err := ParseIndex([]byte(indexFixture))
	if err != nil {
		t.Fatal(err)
	}
	// 16:30:47 EDT (UTC-4 on 31 Oct 2024) = 20:30:47 UTC
	want := time.Date(2024, 10, 31, 20, 30, 47, 0, time.UTC)
	if !p.AcceptedAt.Equal(want) {
		t.Fatalf("AcceptedAt = %v, want %v", p.AcceptedAt, want)
	}
	if len(p.Documents) != 2 {
		t.Fatalf("documents = %d, want 2", len(p.Documents))
	}
	if p.Documents[0].Name != "aapl-20241031.htm" {
		t.Errorf("ix viewer link not unwrapped: %q", p.Documents[0].Name)
	}
	d, ok := p.FindExhibit("EX-99.1", "EX-99")
	if !ok || d.Name != "a8-kex991q4202409282024.htm" {
		t.Errorf("FindExhibit = %+v, %v", d, ok)
	}
}

func TestParseIndexWinterIsEST(t *testing.T) {
	body := `<div class="infoHead">Accepted</div><div class="info">2024-01-25 08:00:00</div>`
	p, err := ParseIndex([]byte(body))
	if err != nil {
		t.Fatal(err)
	}
	if want := time.Date(2024, 1, 25, 13, 0, 0, 0, time.UTC); !p.AcceptedAt.Equal(want) {
		t.Fatalf("got %v want %v", p.AcceptedAt, want)
	}
}

func TestParseIndexMissingAcceptedFails(t *testing.T) {
	if _, err := ParseIndex([]byte("<html></html>")); err == nil {
		t.Fatal("expected error")
	}
}

const submissionsFixture = `{"cik":"320193","name":"Apple Inc.","tickers":["AAPL"],
"filings":{"recent":{
 "accessionNumber":["a1","a2","a3","a4","a5","a6"],
 "filingDate":["2024-11-01","2024-10-31","2024-08-02","2024-08-01","2024-05-03","2024-05-02"],
 "reportDate":["2024-09-28","2024-10-31","2024-06-29","2024-08-01","2024-03-30","2024-05-02"],
 "acceptanceDateTime":["2024-11-01T06:01:36.000Z","2024-10-31T16:30:47.000Z","","","",""],
 "form":["10-K","8-K","10-Q","8-K","10-Q/A","8-K"],
 "items":["","2.02,9.01","","5.07","","2.02,9.01"],
 "primaryDocument":["aapl-20240928.htm","x.htm","q.htm","y.htm","qa.htm","z.htm"]},
 "files":[{"name":"CIK0000320193-submissions-001.json","filingFrom":"1994-01-26","filingTo":"2014-01-01"}]}}`

func TestParseSubmissionsAndSelect(t *testing.T) {
	sub, err := ParseSubmissions([]byte(submissionsFixture))
	if err != nil {
		t.Fatal(err)
	}
	if sub.CIK != "0000320193" {
		t.Errorf("CIK not zero padded: %q", sub.CIK)
	}
	if len(sub.Recent) != 6 || len(sub.OlderPages) != 1 {
		t.Fatalf("recent=%d older=%d", len(sub.Recent), len(sub.OlderPages))
	}
	sel := Selection{PerForm: map[string]int{"10-K": 1, "10-Q": 2, "8-K": 2}, Require8KItem: "2.02"}
	got := sel.Select(sub.Recent)
	var acc []string
	for _, f := range got {
		acc = append(acc, f.AccessionNumber)
	}
	// a4 is an 8-K without Item 2.02; a5 is an amendment (10-Q/A) and not requested.
	want := []string{"a1", "a2", "a3", "a6"}
	if len(acc) != len(want) {
		t.Fatalf("selected %v, want %v", acc, want)
	}
	for i := range want {
		if acc[i] != want[i] {
			t.Fatalf("selected %v, want %v", acc, want)
		}
	}
	if got[0].IndexURL() != "https://www.sec.gov/Archives/edgar/data/320193/a1/a1-index.htm" {
		t.Errorf("IndexURL = %s", got[0].IndexURL())
	}
}

func TestSelectSince(t *testing.T) {
	sub, _ := ParseSubmissions([]byte(submissionsFixture))
	sel := Selection{PerForm: map[string]int{"8-K": 0}, Since: time.Date(2024, 6, 1, 0, 0, 0, 0, time.UTC)}
	if got := sel.Select(sub.Recent); len(got) != 2 {
		t.Fatalf("got %d 8-Ks since June, want 2", len(got))
	}
}

func TestParseSelection(t *testing.T) {
	m, err := ParseSelection("10-K:1, 10-Q:2,8-K:0")
	if err != nil || m["10-K"] != 1 || m["10-Q"] != 2 || m["8-K"] != 0 {
		t.Fatalf("%v %v", m, err)
	}
	for _, bad := range []string{"", "10-K", "10-K:x", "10-K:-1"} {
		if _, err := ParseSelection(bad); err == nil {
			t.Errorf("ParseSelection(%q) should fail", bad)
		}
	}
}

func TestNewClientRequiresContact(t *testing.T) {
	for _, ua := range []string{"", "   ", "Just A Name"} {
		if _, err := NewClient(ua); err == nil {
			t.Errorf("NewClient(%q) should fail", ua)
		}
	}
}

func testClient(t *testing.T) *Client {
	t.Helper()
	c, err := NewClient("Test Suite test@example.com")
	if err != nil {
		t.Fatal(err)
	}
	c.sleep = func(context.Context, time.Duration) error { return nil }
	return c
}

func TestGetRetriesThenSucceeds(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("User-Agent") != "Test Suite test@example.com" {
			t.Errorf("missing User-Agent")
		}
		if atomic.AddInt32(&calls, 1) < 3 {
			w.Header().Set("Retry-After", "1")
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		w.Write([]byte("ok"))
	}))
	defer srv.Close()
	body, err := testClient(t).Get(context.Background(), srv.URL)
	if err != nil || string(body) != "ok" || calls != 3 {
		t.Fatalf("body=%q err=%v calls=%d", body, err, calls)
	}
}

func TestGetDoesNotRetry404(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()
	_, err := testClient(t).Get(context.Background(), srv.URL)
	if err == nil || calls != 1 {
		t.Fatalf("err=%v calls=%d", err, calls)
	}
}

func TestLimiterSpacesRequests(t *testing.T) {
	l := newLimiter(50) // 20ms interval
	start := time.Now()
	for i := 0; i < 6; i++ {
		if err := l.wait(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	if el := time.Since(start); el < 90*time.Millisecond {
		t.Fatalf("6 requests at 50/s took %v, want >= ~100ms", el)
	}
}
