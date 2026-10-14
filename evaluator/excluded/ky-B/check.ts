import assert from 'node:assert/strict';
import {mergeHeaders} from '/task/source/utils/merge.ts';
const h=mergeHeaders({'X-Base':'base','Authorization':'token'},{'x-new':'new'});
assert.equal(h.get('x-base'),'base');assert.equal(h.get('X-New'),'new');
assert.equal(mergeHeaders(h,{authorization:undefined}).get('authorization'),null);
assert.equal(h.get('authorization'),'token');
assert.equal(mergeHeaders(h,{'x-base':'override'}).get('x-base'),'override');
