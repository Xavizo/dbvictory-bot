// Characterizes the real useBot hook without opening sockets or loading Electron.
// Run: node tests/simulations/panel_readiness.cjs [--acceptance]
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const {createRequire} = require("node:module");
const root = path.resolve(__dirname, "../..");
const localRequire = createRequire(path.join(root, "dashboard", "package.json"));
const ts = localRequire("typescript");

function load(name, deps, globals) {
    const file = path.join(root, "dashboard", "lib", name + ".ts");
    const js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
    }).outputText;
    const out = {};
    vm.runInNewContext(js, {exports: out, require(key) {
        if (!(key in deps)) throw Error("Unexpected dependency " + key);
        return deps[key];
    }, ...globals}, {filename: file});
    return out;
}

const panelSession = load("panelSession", {}, {});

async function trial(kind) {
    let now = 0, nextId = 0, fetches = 0, disposed, pending = new Map();
    const slots = [], sockets = [], requests = [];
    const {useBot} = load("useBot", {
        react: {
            useState(initial) {
                const i = slots.push(initial) - 1;
                return [initial, x => {slots[i] = typeof x === "function" ? x(slots[i]) : x;}];
            },
            useRef: current => ({current}), useCallback: fn => fn,
            useEffect(fn) {disposed = fn();}
        },
        "./api": {fetchState: signal => {
            fetches++;
            if (kind === "http-only") return Promise.resolve(JSON.parse(snapshot(55).data));
            assert.ok(requests.every(previous => previous.signal.aborted), "overlapping HTTP reads");
            return new Promise(resolve => requests.push({signal, resolve}));
        }},
        "./auth": {getDashboardToken: async () => "offline-only"},
        "./panelSession": panelSession
    }, {
        window: {dbvAuth: {}}, performance: {now: () => now}, AbortController,
        WebSocket: class FakeSocket {
            static OPEN = 1; readyState = 0;
            constructor(){sockets.push(this);}
            async open(){this.readyState=1;await this.onopen?.();}
            send(){}
            close(){if(this.readyState !== 3){this.readyState = 3; this.onclose?.();}}
        },
        setTimeout: (fn, ms) => timer(fn, ms, false), setInterval: (fn, ms) => timer(fn, ms, true),
        clearInterval: id => pending.delete(id), clearTimeout: id => pending.delete(id)
    });
    function timer(fn, ms, repeat) {
        const id = ++nextId; pending.set(id, {fn, ms, at:now+ms, repeat}); return id;
    }
    async function drain() { for(let i=0;i<6;i++) await Promise.resolve(); }
    async function advance(ms) {
        const end = now+ms;
        for(;;){
            const due = Array.from(pending).filter(([,v]) => v.at<=end).sort((a,b) => a[1].at-b[1].at || a[0]-b[0])[0];
            if (!due) break;
            const [id,t] = due; now = t.at;
            if(t.repeat) t.at+=t.ms; else pending.delete(id);
            t.fn(); await drain();
        }
        now=end;await drain();
    }
    function snapshot(hp){
        const data = {connected:true, actions:[], commands:{session:42}, player:{hp,max_hp:100,level:300,experience:9000000,stats_age:0}};
        if(kind !== "panel-derived") data.session_statistics={status:"active",xp_per_hour:123};
        return {data:JSON.stringify(data)};
    }
    function metrics(){
        return {hp:slots[0]?.player?.hp??null, connected:slots[1], stats:slots[0]?.session_statistics?.status??null,
            http:fetches, sockets:sockets.length};
    }
    const bot = useBot();
    await sockets[0].open();
    if(kind!=="no-initial-frame") sockets[0].onmessage(snapshot(75));
    const initial=metrics();
    if(kind === "closed") sockets[0].close();
    for(let i=0;i<15;i++){
        await advance(1000);
        if(kind === "healthy") sockets[0].onmessage(snapshot(75+i));
        if(kind === "malformed") sockets.at(-1).onmessage?.({data:'{"error":"invalid state"}'});
    }
    const gap=metrics();
    if (kind === "manual-refresh") {
        const previous = requests.at(-1);
        const savedRead = bot.refresh();
        assert.ok(previous.signal.aborted, "manual refresh cancelled the pre-save request");
        requests.at(-1).resolve(JSON.parse(snapshot(51).data));
        await savedRead;
        previous.resolve(JSON.parse(snapshot(1).data));
        await drain();
        assert.equal(metrics().hp, 51, "post-save snapshot wins");
    }
    if (sockets.at(-1).readyState === 3) await advance(2000);
    let active=sockets.at(-1);
    if(active.readyState===0) await active.open();
    if(active.readyState===1) active.onmessage(snapshot(60));
    const recovered=metrics();
    if (sockets[0] !== active) sockets[0].onmessage(snapshot(3));
    for (const req of requests) {
        assert.ok(req.signal.aborted || kind === "manual-refresh" && req === requests.at(-1), "a fresh WS frame supersedes pending HTTP reads");
        req.resolve(JSON.parse(snapshot(1).data));
    }
    await drain();
    assert.equal(metrics().hp,60,"stale socket or HTTP response overwrote newer data");
    disposed();
    const before = metrics();
    active.onmessage(snapshot(5));
    await advance(9000);
    assert.deepEqual(metrics(),before,"updates after unmount");
    assert.equal(pending.size,0,"timer leak");
    assert.equal(initial.hp,kind === "no-initial-frame" ? null : 75);
    return {kind,initial,gap,recovered};
}

(async () => {
    const rows=[];
    for(const kind of ["healthy","engine-silence","panel-derived","closed","no-initial-frame","malformed","http-only","manual-refresh"]) rows.push(await trial(kind));
    console.log(JSON.stringify(rows,null,2));
    assert.equal(rows[0].gap.hp,89); assert.equal(rows[0].gap.http,0);
    assert.ok(rows[3].gap.http>=1);
    const viaHttp = rows.find(row => row.kind === "http-only");
    assert.equal(viaHttp.gap.hp,55);
    assert.equal(viaHttp.gap.connected,true);
    for (const r of rows) assert.equal(r.recovered.hp,60);
    if(process.argv.includes("--acceptance")){
        for(const r of rows.filter(r => ["engine-silence", "panel-derived", "no-initial-frame", "malformed"].includes(r.kind))){
            assert.ok(r.gap.http>0 || r.gap.sockets>1,"no attempt to recover: "+r.kind);
            assert.ok(!r.gap.connected || r.gap.stats==="stale","old data still current: "+r.kind);
        }
    }
})().catch(err=>{console.error(err);process.exitCode=1;});
