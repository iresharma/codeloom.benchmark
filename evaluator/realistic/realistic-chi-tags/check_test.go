package chi

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestBenchmarkReset verifies chi's route tagging feature: tags attached
// via Tag() (applied per-route through With()) are discoverable afterwards
// through Mux.RoutesWithTags(), tags accumulate in order when multiple
// Tag() calls are chained, untagged routes report no tags, and Tag()
// remains a transparent pass-through middleware that does not affect
// request handling.
func TestBenchmarkReset(t *testing.T) {
	h := func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}

	r := NewRouter()
	r.With(Tag("auth-required"), Tag("admin")).Get("/admin", h)
	r.With(Tag("public")).Post("/signup", h)
	r.Get("/plain", h)

	got := map[string][]string{}
	for _, rt := range r.RoutesWithTags() {
		got[rt.Method+" "+rt.Pattern] = rt.Tags
	}

	adminTags := got["GET /admin"]
	if len(adminTags) != 2 || adminTags[0] != "auth-required" || adminTags[1] != "admin" {
		t.Fatalf("expected tags [auth-required admin] on GET /admin, got %v", adminTags)
	}

	signupTags := got["POST /signup"]
	if len(signupTags) != 1 || signupTags[0] != "public" {
		t.Fatalf("expected tags [public] on POST /signup, got %v", signupTags)
	}

	if plainTags := got["GET /plain"]; len(plainTags) != 0 {
		t.Fatalf("expected no tags on GET /plain, got %v", plainTags)
	}

	// Tag() must not interfere with normal request handling.
	req := httptest.NewRequest(http.MethodGet, "/admin", nil)
	rr := httptest.NewRecorder()
	r.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("expected tagged route to still serve requests normally, got status %d", rr.Code)
	}
}
