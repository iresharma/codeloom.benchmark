import assert from 'node:assert/strict';import PQueue from '/task/source/index.ts';
const q=new PQueue({autoStart:false,concurrency:1});
const promises=[q.add(async()=>1,{id:'a',priority:1}),q.add(async()=>2,{id:'b',priority:2})];
assert.deepEqual(q.pendingIds(),['b','a']);const ids=q.pendingIds() as string[];ids.reverse();assert.deepEqual(q.pendingIds(),['b','a']);
q.setPriority('a',3);assert.deepEqual(q.pendingIds(),['a','b']);q.start();await Promise.all(promises);assert.deepEqual(q.pendingIds(),[]);
