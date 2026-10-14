import assert from 'node:assert/strict';import PriorityQueue from '/task/source/priority-queue.ts';
const q=new PriorityQueue();const a=async()=>1,b=async()=>2,c=async()=>3;
q.enqueue(a,{id:'a',priority:0});q.enqueue(b,{id:'b',priority:0});q.setPriority('a',5);assert.equal(q.dequeue(),a);assert.throws(()=>q.setPriority('missing',2),ReferenceError);
q.enqueue(c,{id:'c',priority:1});q.setPriority('b',3);assert.equal(q.dequeue(),b);assert.equal(q.dequeue(),c);
