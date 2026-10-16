import assert from 'node:assert/strict';
import PQueue from '/task/source/index.ts';
import PriorityQueue from '/task/source/priority-queue.ts';
const q=new PQueue({autoStart:false,concurrency:1});const order:string[]=[];
const promises=['a','b','c'].map(id=>q.add(async()=>{order.push(id);return id;},{id,priority:0}));
q.setPriorities([{id:'c',priority:2},{id:'c',priority:3}]);
assert.throws(()=>q.setPriorities([{id:'a',priority:100},{id:'missing',priority:1}]),ReferenceError);
assert.throws(()=>q.setPriorities([{id:'a',priority:NaN}]),TypeError);
q.setPriorities([]);q.start();await Promise.all(promises);assert.deepEqual(order,['c','a','b']);
const p=new PriorityQueue();p.enqueue(async()=>1,{id:'a'});p.setPriorities([{id:'a',priority:5}]);assert.equal(p.size,1);

class LegacyQueue extends PriorityQueue { constructor(){super();(this as any).setPriorities=undefined;} }
const legacy=new PQueue({queueClass:LegacyQueue,autoStart:false,concurrency:1});const legacyOrder:string[]=[];
const pending=['a','b'].map(id=>legacy.add(async()=>{legacyOrder.push(id);},{id,priority:0}));
legacy.setPriorities([{id:'b',priority:2}]);assert.throws(()=>legacy.setPriorities([{id:'a',priority:9},{id:'missing',priority:1}]),ReferenceError);
legacy.start();await Promise.all(pending);assert.deepEqual(legacyOrder,['b','a']);
