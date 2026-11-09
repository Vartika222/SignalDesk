package edgar

import (
	"fmt"
	"html"
	"regexp"
	"strings"
	"time"
	_ "time/tzdata" // embed tz database so America/New_York works everywhere
)

// IndexPage is what we need from a filing's -index.htm.
type IndexPage struct {
	AcceptedAt time.Time // converted to UTC from US/Eastern
	Documents  []IndexDoc
}

type IndexDoc struct {
	Seq         string
	Description string
	Name        string // file name inside the filing folder
	Type        string // e.g. 10-K, EX-99.1
}

var (
	reAccepted = regexp.MustCompile(`(?s)<div class="infoHead">\s*Accepted\s*</div>\s*<div class="info">\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})\s*</div>`)
	reTable    = regexp.MustCompile(`(?is)<table[^>]*summary="Document Format Files"[^>]*>(.*?)</table>`)
	reRow      = regexp.MustCompile(`(?is)<tr[^>]*>(.*?)</tr>`)
	reCell     = regexp.MustCompile(`(?is)<td[^>]*>(.*?)</td>`)
	reHref     = regexp.MustCompile(`(?i)href="([^"]+)"`)
	reTag      = regexp.MustCompile(`(?s)<[^>]+>`)
)

var eastern = mustLoad("America/New_York")

func mustLoad(name string) *time.Location {
	loc, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return loc
}

// ParseIndex extracts the acceptance time and the document table.
func ParseIndex(body []byte) (*IndexPage, error) {
	s := string(body)
	m := reAccepted.FindStringSubmatch(s)
	if m == nil {
		return nil, fmt.Errorf("index page: no Accepted field")
	}
	local, err := time.ParseInLocation("2006-01-02 15:04:05", m[1], eastern)
	if err != nil {
		return nil, fmt.Errorf("index page: accepted %q: %w", m[1], err)
	}
	page := &IndexPage{AcceptedAt: local.UTC()}

	t := reTable.FindStringSubmatch(s)
	if t == nil {
		return page, nil
	}
	for _, row := range reRow.FindAllStringSubmatch(t[1], -1) {
		cells := reCell.FindAllStringSubmatch(row[1], -1)
		if len(cells) < 4 {
			continue // header row uses <th>
		}
		href := reHref.FindStringSubmatch(cells[2][1])
		if href == nil {
			continue
		}
		page.Documents = append(page.Documents, IndexDoc{
			Seq:         clean(cells[0][1]),
			Description: clean(cells[1][1]),
			Name:        fileName(href[1]),
			Type:        clean(cells[3][1]),
		})
	}
	return page, nil
}

// FindExhibit returns the first document whose type is exactly want, else the first with the prefix
// (EX-99.1 is sometimes filed as EX-99 or EX-99.01).
func (p *IndexPage) FindExhibit(want, prefix string) (IndexDoc, bool) {
	for _, d := range p.Documents {
		if strings.EqualFold(d.Type, want) {
			return d, true
		}
	}
	for _, d := range p.Documents {
		if strings.HasPrefix(strings.ToUpper(d.Type), strings.ToUpper(prefix)) {
			return d, true
		}
	}
	return IndexDoc{}, false
}

func clean(s string) string {
	return strings.TrimSpace(html.UnescapeString(reTag.ReplaceAllString(s, "")))
}

// fileName handles plain Archive links and inline-XBRL viewer links (/ix?doc=/Archives/...).
func fileName(href string) string {
	href = strings.TrimPrefix(href, "/ix?doc=")
	if i := strings.LastIndex(href, "/"); i >= 0 {
		return href[i+1:]
	}
	return href
}
