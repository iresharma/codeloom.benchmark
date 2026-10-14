package chi_test
import("testing";"net/http";"net/http/httptest";"github.com/go-chi/chi/v5/middleware")
func TestBenchmarkReset(t *testing.T){
 config:=http.Header{"x-test": []string{"one","two"}}
 wrap:=middleware.SetHeaders(config);config["x-test"][0]="bad";config.Set("X-New","bad")
 for i:=0;i<2;i++{
  w:=httptest.NewRecorder();w.Header().Set("X-Other","keep");w.Header().Set("X-Test","old")
  wrap(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
   v:=w.Header().Values("X-Test");if len(v)!=2||v[0]!="one"||v[1]!="two"{t.Fatal("snapshot or header replacement")}
   if w.Header().Get("X-Other")!="keep"||w.Header().Get("X-New")!=""{t.Fatal("unlisted headers")}
   w.Header()["X-Test"][0]="changed"
  })).ServeHTTP(w,httptest.NewRequest("GET","/",nil))
 }
 middleware.SetHeaders(nil)(http.HandlerFunc(func(http.ResponseWriter,*http.Request){})).ServeHTTP(httptest.NewRecorder(),httptest.NewRequest("GET","/",nil))
}
