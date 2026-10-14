import assert from 'node:assert/strict';
import ky, {HTTPError} from '/task/source/index.ts';
try { await ky('https://example.test',{headers:{Authorization:'secret',Cookie:'session', 'X-Key':'private','X-Public':'ok'},errorHeaderRedaction:['X-KEY'],retry:0,fetch:async()=>new Response('bad',{status:400})});assert.fail('expected HTTPError'); }
catch(e) {assert.ok(e instanceof HTTPError);assert.equal(e.requestHeaderSummary.authorization,'[REDACTED]');assert.equal(e.requestHeaderSummary.cookie,'[REDACTED]');assert.equal(e.requestHeaderSummary['x-key'],'[REDACTED]');assert.equal(e.requestHeaderSummary['x-public'],'ok');assert.equal(e.request.headers.get('authorization'),'secret');}
