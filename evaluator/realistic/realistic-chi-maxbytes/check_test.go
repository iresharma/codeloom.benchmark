package chi_test

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

func readBody(w http.ResponseWriter, r *http.Request) {
	_, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusRequestEntityTooLarge)
		return
	}
	w.WriteHeader(http.StatusOK)
}

// TestBenchmarkReset verifies middleware.MaxBytes enforces a per-route body
// size limit: an over-limit request body is rejected, a same-size body is
// accepted on a route configured with a higher limit, and an under-limit
// request on the strict route still succeeds.
func TestBenchmarkReset(t *testing.T) {
	r := chi.NewRouter()
	r.With(middleware.MaxBytes(8)).Post("/strict", readBody)
	r.With(middleware.MaxBytes(1024)).Post("/lenient", readBody)

	longBody := "this body is definitely longer than eight bytes"

	req := httptest.NewRequest(http.MethodPost, "/strict", strings.NewReader(longBody))
	rr := httptest.NewRecorder()
	r.ServeHTTP(rr, req)
	if rr.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected strict route (limit=8) to reject long body, got status %d", rr.Code)
	}

	req = httptest.NewRequest(http.MethodPost, "/lenient", strings.NewReader(longBody))
	rr = httptest.NewRecorder()
	r.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("expected lenient route (limit=1024) to accept long body, got status %d", rr.Code)
	}

	req = httptest.NewRequest(http.MethodPost, "/strict", strings.NewReader("short"))
	rr = httptest.NewRecorder()
	r.ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("expected strict route to accept a short body, got status %d", rr.Code)
	}
}
