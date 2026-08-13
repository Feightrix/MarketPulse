const START=100;
const symbols=['SPY','QQQ','AAPL','NVDA','MSFT'];
let rngSeed=872341;
function rand(){rngSeed=(rngSeed*1664525+1013904223)%4294967296;return rngSeed/4294967296}
let state;
function makeAsset(sym,i){return {sym,price:100+(i*7.5),history:Array.from({length:55},(_,k)=>100+(i*7.5)+Math.sin(k/7+i)*1.2),drift:(i===1||i===3)?.0012:.00045,vol:.006+i*.0005,momentum:0,trend:0,score:0,signal:'WAIT'} }
function reset(){
 rngSeed=872341; state={cash:START,position:null,trades:[],assets:symbols.map(makeAsset),tick:0,running:false,peak:START,maxDD:0,wins:0,losses:0,selected:'SPY'}; render(); explain('Idle','MarketPulse is waiting for a simulation cycle. When it acts, this panel explains why it bought, held, or exited.');
}
function ema(arr,n){let a=2/(n+1),v=arr[0];for(let i=1;i<arr.length;i++)v=a*arr[i]+(1-a)*v;return v}
function calcAsset(a){let h=a.history, last=h[h.length-1], prev=h[h.length-6]; a.momentum=(last/prev-1)*100; let e6=ema(h.slice(-20),6), e16=ema(h.slice(-30),16);a.trend=(e6/e16-1)*100; a.score=a.momentum*.58+a.trend*1.9; a.signal=a.score>.45&&a.trend>.05?'BUY':a.score<-.4?'SELL':'WAIT'}
function nextPrice(a){
 let cyc=Math.sin((state.tick+symbols.indexOf(a.sym)*11)/14)*.0018; let shock=(rand()-.5)*2*a.vol; let mean=(100+symbols.indexOf(a.sym)*7.5-a.price)*.0002; let p=a.price*(1+a.drift+cyc+shock+mean); a.price=Math.max(5,p);a.history.push(a.price);if(a.history.length>120)a.history.shift();calcAsset(a)
}
function portfolio(){return state.cash+(state.position?state.position.qty*asset(state.position.sym).price:0)}
function asset(sym){return state.assets.find(a=>a.sym===sym)}
function riskParams(){return {risk:+riskPct.value/100,capital:+capitalPct.value/100,stop:+stopPct.value/100,target:+targetPct.value/100}}
function buy(a){let p=riskParams(),equity=portfolio(),riskDollars=START*p.risk, perShare=a.price*p.stop;let qtyRisk=riskDollars/perShare,qtyCap=(equity*p.capital)/a.price,qty=Math.max(0,Math.min(qtyRisk,qtyCap,state.cash/a.price));if(qty<.001)return false;let cost=qty*a.price;state.cash-=cost;state.position={sym:a.sym,entry:a.price,qty,stop:a.price*(1-p.stop),target:a.price*(1+p.target),entryTick:state.tick};state.trades.push({sym:a.sym,action:'BUY',price:a.price,qty,pl:null,reason:`Signal score ${a.score.toFixed(2)}`});state.selected=a.sym;explain('Bought',`${a.sym} produced the strongest qualifying signal (${a.score.toFixed(2)}). Position size was capped by both risk-per-trade and maximum-capital rules.`);return true}
function close(reason){let pos=state.position,a=asset(pos.sym),proceeds=pos.qty*a.price,pl=(a.price-pos.entry)*pos.qty;state.cash+=proceeds;state.trades.push({sym:pos.sym,action:'SELL',price:a.price,qty:pos.qty,pl,reason});if(pl>=0)state.wins++;else state.losses++;state.position=null;explain('Exited',`${a.sym} was closed because ${reason.toLowerCase()}. Realized P/L: ${money(pl)}.`)}
function cycle(){state.tick++;state.assets.forEach(nextPrice);setLogic(0);if(state.position){setLogic(3);let a=asset(state.position.sym);let p=state.position;if(a.price<=p.stop)close('Stop loss reached');else if(a.price>=p.target)close('Profit target reached');else if(a.signal==='SELL'&&state.tick-p.entryTick>3)close('Signal reversed');else explain('Managing',`${a.sym} remains open. MarketPulse is watching the stop (${money(p.stop)}) and profit target (${money(p.target)}).`)}else{setLogic(1);let candidates=[...state.assets].filter(a=>a.signal==='BUY').sort((a,b)=>b.score-a.score);if(candidates.length&&state.tick>2){setLogic(2);buy(candidates[0])}else explain('Scanning','No symbol currently satisfies the Phase 1 entry threshold, so MarketPulse stays in cash.')}
 let eq=portfolio();state.peak=Math.max(state.peak,eq);state.maxDD=Math.max(state.maxDD,(state.peak-eq)/state.peak);render()}
function setLogic(n){for(let i=0;i<5;i++)document.getElementById('l'+i).classList.toggle('active',i===n);cycleBadge.textContent=['Scanning','Scoring','Sizing','Managing','Exiting'][n]}
function explain(title,text){reasonBadge.textContent=title;reasonText.textContent=text}
function money(x){let s=x<0?'-$':'$';return s+Math.abs(x).toFixed(2)}
function render(){let eq=portfolio(),pl=eq-START,pct=pl/START*100;cycleCount.textContent=state.tick;portfolioEl.textContent=money(eq);cash.textContent=money(state.cash);netpl.textContent=money(pl);netpl.className='value '+(pl>0?'up':pl<0?'down':'');netplPct.textContent=(pct>=0?'+':'')+pct.toFixed(2)+'%';portfolioDelta.textContent=pl===0?'Starting capital':`${pl>=0?'+':''}${pct.toFixed(2)}% since start`;trades.textContent=state.trades.filter(t=>t.action==='SELL').length;let closed=state.wins+state.losses;winrate.textContent=(closed?Math.round(state.wins/closed*100):0)+'% win rate';drawdown.textContent=(state.maxDD*100).toFixed(2)+'%';riskDisplay.textContent=money(START*(+riskPct.value/100));engineStatus.textContent=state.running?'Simulation running':'Simulation paused / ready';drawChart();watchRows.innerHTML=state.assets.map(a=>`<tr onclick="state.selected='${a.sym}';drawChart()" style="cursor:pointer"><td class="sym">${a.sym}</td><td class="right">${money(a.price)}</td><td class="right ${a.momentum>=0?'up':'down'}">${a.momentum.toFixed(2)}%</td><td class="right ${a.trend>=0?'up':'down'}">${a.trend.toFixed(2)}%</td><td class="center"><span class="pill ${a.signal.toLowerCase()}">${a.signal}</span></td><td class="right">${a.score.toFixed(2)}</td></tr>`).join('');renderPosition();renderTrades()}
function renderPosition(){if(!state.position){positionBadge.textContent='None';positionBox.className='empty';positionBox.textContent='MarketPulse is currently holding cash.';return}let p=state.position,a=asset(p.sym),pl=(a.price-p.entry)*p.qty,pct=(a.price/p.entry-1)*100;positionBadge.textContent='OPEN';positionBox.className='position';let range=Math.max(0,Math.min(100,(a.price-p.stop)/(p.target-p.stop)*100));positionBox.innerHTML=`<div class="pos-row"><span>Symbol</span><b>${p.sym}</b></div><div class="pos-row"><span>Entry</span><b>${money(p.entry)}</b></div><div class="pos-row"><span>Current</span><b>${money(a.price)}</b></div><div class="pos-row"><span>Shares</span><b>${p.qty.toFixed(4)}</b></div><div class="pos-row"><span>Unrealized P/L</span><b class="${pl>=0?'up':'down'}">${money(pl)} (${pct.toFixed(2)}%)</b></div><div class="pos-row"><span>Stop / Target</span><b>${money(p.stop)} / ${money(p.target)}</b></div><div class="meter"><div style="width:${range}%"></div></div>`}
function renderTrades(){if(!state.trades.length){tradeRows.innerHTML='<tr><td colspan="7" class="empty">No trades yet. Start or step the simulator.</td></tr>';return}tradeRows.innerHTML=[...state.trades].reverse().map((t,i)=>`<tr><td>${state.trades.length-i}</td><td class="sym">${t.sym}</td><td><span class="pill ${t.action==='BUY'?'buy':'sell'}">${t.action}</span></td><td class="right">${money(t.price)}</td><td class="right">${t.qty.toFixed(4)}</td><td class="right ${t.pl==null?'':t.pl>=0?'up':'down'}">${t.pl==null?'—':money(t.pl)}</td><td>${t.reason}</td></tr>`).join('')}
function drawChart(){let c=chart,ctx=c.getContext('2d'),dpr=window.devicePixelRatio||1,rect=c.getBoundingClientRect();c.width=rect.width*dpr;c.height=rect.height*dpr;ctx.scale(dpr,dpr);let w=rect.width,h=rect.height;ctx.clearRect(0,0,w,h);let a=asset(state.selected)||state.assets[0],arr=a.history.slice(-70),min=Math.min(...arr),max=Math.max(...arr),pad=(max-min||1)*.15;min-=pad;max+=pad;ctx.strokeStyle='#142839';ctx.lineWidth=1;for(let i=1;i<5;i++){let y=h*i/5;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke()}ctx.strokeStyle='#54d3ff';ctx.lineWidth=2;ctx.beginPath();arr.forEach((v,i)=>{let x=i/(arr.length-1)*w,y=h-(v-min)/(max-min)*h;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();ctx.strokeStyle='rgba(157,241,184,.65)';ctx.lineWidth=1.3;let fast=[];for(let i=0;i<arr.length;i++)fast.push(ema(arr.slice(Math.max(0,i-10),i+1),6));ctx.beginPath();fast.forEach((v,i)=>{let x=i/(fast.length-1)*w,y=h-(v-min)/(max-min)*h;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();chartSymbol.textContent=a.sym;chartPrice.textContent=money(a.price)}
function exportCSV(){let lines=['#,Symbol,Action,Price,Quantity,PL,Reason'];state.trades.forEach((t,i)=>lines.push([i+1,t.sym,t.action,t.price.toFixed(4),t.qty.toFixed(6),t.pl==null?'':t.pl.toFixed(4),`"${t.reason.replaceAll('"','""')}"`].join(',')));let blob=new Blob([lines.join('\n')],{type:'text/csv'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='marketpulse_phase1_trades.csv';a.click();URL.revokeObjectURL(url)}
let timer=null;function start(){if(state.running)return;state.running=true;startBtn.disabled=true;startBtn.textContent='Running…';pauseBtn.disabled=false;cycle();timer=setInterval(cycle,650);render()}function pause(){state.running=false;clearInterval(timer);timer=null;startBtn.disabled=false;startBtn.textContent='Start';pauseBtn.disabled=true;render()}
const portfolioEl=document.getElementById('portfolio');
const portfolioDelta=document.getElementById('portfolioDelta');
const cash=document.getElementById('cash');
const netpl=document.getElementById('netpl');
const netplPct=document.getElementById('netplPct');
const trades=document.getElementById('trades');
const winrate=document.getElementById('winrate');
const riskDisplay=document.getElementById('riskDisplay');
const drawdown=document.getElementById('drawdown');
const cycleCount=document.getElementById('cycleCount');
const engineStatus=document.getElementById('engineStatus');
const startBtn=document.getElementById('startBtn');
const stepBtn=document.getElementById('stepBtn');
const pauseBtn=document.getElementById('pauseBtn');
const resetBtn=document.getElementById('resetBtn');
const exportBtn=document.getElementById('exportBtn');
const chart=document.getElementById('chart');
const chartSymbol=document.getElementById('chartSymbol');
const chartPrice=document.getElementById('chartPrice');
const watchRows=document.getElementById('watchRows');
const cycleBadge=document.getElementById('cycleBadge');
const tradeRows=document.getElementById('tradeRows');
const positionBadge=document.getElementById('positionBadge');
const positionBox=document.getElementById('positionBox');
const reasonBadge=document.getElementById('reasonBadge');
const reasonText=document.getElementById('reasonText');
const riskPct=document.getElementById('riskPct');
const capitalPct=document.getElementById('capitalPct');
const stopPct=document.getElementById('stopPct');
const targetPct=document.getElementById('targetPct');
startBtn.onclick=start;pauseBtn.onclick=pause;stepBtn.onclick=()=>{pause();cycle()};resetBtn.onclick=()=>{pause();reset()};exportBtn.onclick=exportCSV;[riskPct,capitalPct,stopPct,targetPct].forEach(el=>el.onchange=render);window.addEventListener('resize',drawChart);reset();