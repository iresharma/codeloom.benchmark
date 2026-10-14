package chi
import "testing"
func TestBenchmarkReset(t *testing.T) {
 c:=NewRouteContext(); c.URLParams.Add("id","old"); c.RoutePath="/old"; c.Reset()
 if len(c.URLParams.Keys)!=0 || len(c.URLParams.Values)!=0 || c.URLParam("id")!="" || c.RoutePath!="" { t.Fatal("request context not cleared") }
 c.URLParams.Add("id","new"); if c.URLParam("id")!="new" {t.Fatal("reuse failed")}
}
