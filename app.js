const $ = (id) => document.getElementById(id);

const state = {
  quiz: null,
  index: 0,
  answers: {},
  locked: false,
  soundOn: true,
  factsShown: new Set(), // 本局已解锁的 fact id
};

const TYPE_META = {
  business: { label: '业务题', score: 30 },
  hot: { label: '热点题', score: 20 },
  timing: { label: '时机题', score: 50 },
};

// ==================== 音频（Web Audio 合成，零外部素材） ==================== //
const audio = {
  ctx: null,
  master: null,
  bgmTimer: null,
  bgmStep: 0,
};

function ensureAudio() {
  if (audio.ctx) return audio.ctx;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audio.ctx = new Ctx();
    audio.master = audio.ctx.createGain();
    audio.master.gain.value = 0.7;
    audio.master.connect(audio.ctx.destination);
  } catch (e) {
    console.warn('AudioContext 不可用', e);
  }
  return audio.ctx;
}

function resumeAudio() {
  ensureAudio();
  if (audio.ctx && audio.ctx.state === 'suspended') {
    audio.ctx.resume().catch(() => {});
  }
}

function beep(freq, durMs, { type = 'sine', gain = 0.18, attack = 0.005, release = 0.08, when = 0 } = {}) {
  if (!state.soundOn) return;
  const ctx = ensureAudio();
  if (!ctx) return;
  const t0 = ctx.currentTime + when;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(gain, t0 + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + attack + durMs / 1000 + release);
  osc.connect(g); g.connect(audio.master);
  osc.start(t0);
  osc.stop(t0 + attack + durMs / 1000 + release + 0.02);
}

function chord(freqs, durMs, type = 'triangle', gain = 0.12) {
  freqs.forEach(f => beep(f, durMs, { type, gain }));
}

// 游戏事件音效
function sfx(type) {
  switch (type) {
    case 'click': beep(660, 60, { type: 'square', gain: 0.12 }); break;
    case 'pick': beep(523, 80, { type: 'triangle' }); beep(784, 120, { type: 'triangle', when: 0.06 }); break;
    case 'correct': chord([523, 659, 784], 180, 'triangle', 0.12); break;
    case 'wrong': beep(220, 200, { type: 'sawtooth', gain: 0.16 }); beep(160, 240, { type: 'sawtooth', when: 0.08 }); break;
    case 'finish': [523, 659, 784, 1046].forEach((f, i) => beep(f, 200, { type: 'triangle', when: i * 0.12 })); break;
    case 'unlock': [392, 523, 659, 880].forEach((f, i) => beep(f, 140, { type: 'sine', gain: 0.16, when: i * 0.05 })); break;
    default: break;
  }
}

// BGM：用循环轻旋律 + 低音 pad
const BGM_MELODY = [
  // [bar1]    [bar2]      [bar3]      [bar4]
  523, 587, 659, 784,  659, 587, 523, 0,
  440, 523, 587, 659,  587, 523, 440, 0,
  392, 440, 523, 659,  587, 523, 440, 0,
  523, 659, 784, 880,  784, 659, 523, 0,
];
const BGM_BASS = [131, 131, 110, 110, 98, 98, 131, 131];

function startBGM() {
  if (!state.soundOn) return;
  ensureAudio();
  if (!audio.ctx || audio.bgmTimer) return;
  const stepMs = 320; // 节拍
  audio.bgmStep = 0;
  const tick = () => {
    if (!state.soundOn) return;
    const i = audio.bgmStep % BGM_MELODY.length;
    const note = BGM_MELODY[i];
    if (note > 0) {
      beep(note, stepMs * 0.9, { type: 'triangle', gain: 0.05, release: 0.05 });
    }
    // 低音 pad 每 4 拍换
    if (i % 4 === 0) {
      const bass = BGM_BASS[(audio.bgmStep / 4 | 0) % BGM_BASS.length];
      beep(bass, stepMs * 4, { type: 'sine', gain: 0.04, attack: 0.05, release: 0.4 });
    }
    audio.bgmStep++;
  };
  tick();
  audio.bgmTimer = setInterval(tick, stepMs);
}

function stopBGM() {
  if (audio.bgmTimer) {
    clearInterval(audio.bgmTimer);
    audio.bgmTimer = null;
  }
}

function setSound(on) {
  state.soundOn = on;
  $('btn-sound').textContent = on ? '声音 开' : '声音 关';
  $('btn-sound').classList.toggle('off', !on);
  if (on) { resumeAudio(); startBGM(); }
  else { stopBGM(); }
}

// ==================== 硬核科技知识库 ==================== //
const TECH_FACTS = [
  { id: 'f_litho', title: '为什么 7nm 以下离不开 EUV？',
    body: 'DUV 光刻机受瑞利判据限制，单次曝光特征尺寸约 130nm 半 pitch；要继续往下，必须借助波长仅 13.5nm 的 EUV。中芯国际、中微公司等正在攻关的，正是这道“光学天花板”。' },
  { id: 'f_hbm', title: 'HBM 为什么是 AI 算力的命门？',
    body: '大模型推理对显存带宽极度敏感。HBM 通过 TSV 把多层 DRAM 堆叠并紧贴 GPU，单颗 HBM3E 带宽可达 1TB/s 以上。海力士、三星、美光三分天下，国产替代仍在追赶。' },
  { id: 'f_igbt', title: 'IGBT 为何被称为电力电子的“CPU”？',
    body: 'IGBT 是中高压电力变换的核心开关，决定新能源汽车、光伏逆变器、高铁牵引的效率上限。时代电气、斯达半导等正在 1200V/3300V 车规级与工规级上突破。' },
  { id: 'f_kline_ma', title: '为什么 MA5 上穿 MA20 会被称为“金叉”？',
    body: '短期均线（5 日）反映最新资金动能，长期均线（20 日）代表中期成本。短期向上穿越长期，通常意味着买盘加速，但需配合成交量，否则可能是诱多。' },
  { id: 'f_pe_neg', title: 'PE 为负数 / 极高，真的不能买吗？',
    body: 'PE = 股价 / 每股收益。亏损公司 PE 显示为负；高速成长期的公司（如寒武纪）PE 也可能极高。投资者更应关注营收增速、研发投入与未来现金流折现。' },
  { id: 'f_qfq', title: '前复权为什么比不复权更适合回看涨跌？',
    body: '除权除息（送股、分红、配股）会造成 K 线断崖。前复权以最新价为基准反推历史价格，能直接看出真实持有收益率，是计算“近期涨跌”的标准口径。' },
  { id: 'f_chiplet', title: 'Chiplet：为什么摩尔定律走不动了，反而更香？',
    body: 'Chiplet 把大芯片切成多颗小芯粒，再通过先进封装（2.5D/3D）拼合，可在同等良率下实现更高集成度，是国产算力绕开先进制程的关键路径之一。' },
  { id: 'f_pv_topcon', title: 'TOPCon 与 HJT：光伏下一战',
    body: 'TOPCon 在 P-PERC 基础上叠加隧穿氧化层，效率更高、与现有产线兼容；HJT 用异质结本征薄膜做钝化，工艺更短但成本更高。当前 TOPCon 是扩产主力。' },
  { id: 'f_catl_kirin', title: '“麒麟电池”到底强在哪？',
    body: '宁德时代麒麟电池改用 CTC（cell-to-chassis）结构与水冷钣金，体积利用率从 55% 提升到 72%，等效系统能量密度达 255Wh/kg，直接改善整车续航与快充。' },
  { id: 'f_cro', title: '为什么 CRO 在创新药链条里赚得最稳？',
    body: 'CRO 收取研发服务费，不承担药品上市风险。管线越多、订单越大，营收越可预测。但美联储加息会压低 Biotech 融资，进而冲击 CRO 新签订单。' },
  { id: 'f_optic_800g', title: '800G / 1.6T 光模块为什么和 AI 强绑定？',
    body: 'GPU 集群训练需要海量数据交换，单台交换机端口数×速率决定集群规模。光模块从 400G 跳到 800G、再到 1.6T，速率每翻倍一轮，背后都是一次算力扩张周期。' },
  { id: 'f_fabless', title: 'Fabless vs IDM：晶圆代工的商业模式',
    body: 'IDM（如英特尔、三星）自建工厂；Fabless（如海光、寒武纪）只设计不生产，把晶圆交给中芯、台积电代工。这决定了 Fabless 公司的“产能”受限于代工厂排期。' },
  { id: 'f_robotics', title: '谐波减速器 vs RV 减速器',
    body: '谐波减速器体积小、传动比大，用于机器人小臂、腕部；RV 减速器承载强、抗冲击，用于大臂、底座。绿的谐波、中大力德等正在国产替代日本哈默纳科。' },
  { id: 'f_quantum', title: '量子计算“量子优越性”到底是什么？',
    body: '量子比特叠加与纠缠能在特定问题（如随机线路采样）上指数级超越经典计算机，但通用纠错量子计算仍需 10 年以上。国盾量子等以量子保密通信为主业落地。' },
  { id: 'f_haiguang_dcu', title: '海光 DCU 为什么对标英伟达？',
    body: '海光 DCU 基于 AMD Zen+GFX 架构授权，兼容 CUDA 生态的 ROCm/HIP 软件栈，是国内少数能跑大模型训练+推理的国产 GPGPU 之一。' },
  { id: 'f_storage', title: '存储芯片的周期：DRAM 与 NAND',
    body: '存储是标准化大宗品，价格随供需剧烈波动。下游服务器、手机需求决定景气度。兆易创新、长江存储、北京君正（DRAM）是国产替代关键棋子。' },
  { id: 'f_sima_car', title: '智能汽车：域控制器是什么？',
    body: '传统汽车 ECU 各管一摊，域控制器把智驾、座舱、动力合并到几个高性能 SoC 上。经纬恒润、德赛西威等是国产域控 Tier1，受高通 8295、地平线 J6 等芯片供给影响。' },
  { id: 'f_data_center', title: 'AIDC：AI 时代的数据中心',
    body: 'AIDC 单机柜功率从传统 6kW 跃升到 30~100kW，对液冷、UPS、市电容量提出量级挑战。光环新网、奥飞数据等 IDC 厂商的核心竞争点是电力与液冷改造能力。' },
  { id: 'f_riscv', title: 'RISC-V：开源指令集的中国机会',
    body: 'RISC-V 开源免授权费，是国产 MCU 与定制算力绕开 ARM/x86 的路径。乐鑫、全志、中科蓝讯等已在物联网、音频、AIoT 领域形成规模出货。' },
  { id: 'f_security', title: '为什么信创会反复成为科技股主线？',
    body: '信创=信息技术应用创新，要求党政军与关键行业优先采购国产 CPU/OS/数据库/中间件。这是金山办公、达梦、奇安信、东方通、宝兰德等公司稳定的“政策刚需”需求。' },
];

function pickRandomFact() {
  const pool = TECH_FACTS.filter(f => !state.factsShown.has(f.id));
  if (!pool.length) return null;
  const f = pool[Math.floor(Math.random() * pool.length)];
  state.factsShown.add(f.id);
  return f;
}

function showFact(fact) {
  if (!fact) return;
  $('fact-title').textContent = fact.title;
  $('fact-body').textContent = fact.body;
  $('fact-modal').classList.remove('hidden');
  sfx('unlock');
}

function closeFact(cb) {
  $('fact-modal').classList.add('hidden');
  if (typeof cb === 'function') cb();
}

// ==================== 屏幕 / 工具函数 ==================== //
function showScreen(name) {
  ['start', 'quiz', 'result'].forEach(s => {
    $(`screen-${s}`).classList.toggle('active', s === name);
  });
}

function toast(text, ms = 1600) {
  const el = $('toast');
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('hidden'), ms);
}

function fmtPct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--';
  const n = Number(v);
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function retClass(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '';
  return Number(v) >= 0 ? 'positive' : 'negative';
}

function safeText(v) {
  return v === null || v === undefined || v === '' ? '暂缺' : v;
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
  return data;
}

// ==================== 游戏主流程 ==================== //
async function startGame() {
  resumeAudio();
  if (state.soundOn) startBGM();
  state.factsShown = new Set();
  try {
    toast('正在生成题包...');
    $('btn-start').disabled = true;
    const quiz = await fetchJSON('api/quiz?n=8');
    state.quiz = quiz;
    state.index = 0;
    state.answers = {};
    state.locked = false;
    showScreen('quiz');
    renderQuestion();
  } catch (e) {
    toast(e.message || '加载失败');
  } finally {
    $('btn-start').disabled = false;
  }
}

function renderQuestion() {
  const quiz = state.quiz;
  const q = quiz.questions[state.index];
  const meta = TYPE_META[q.type] || { label: q.type, score: 0 };

  $('progress-text').textContent = `${state.index + 1}/${quiz.questions.length}`;
  $('score-preview').textContent = `已答 ${Object.keys(state.answers).length}`;
  $('progress-bar').style.width = `${(state.index) / quiz.questions.length * 100}%`;

  $('q-type').textContent = `${meta.label} · ${meta.score} 分`;
  $('q-company').textContent = q.company;
  $('q-prompt').textContent = q.prompt;

  const ctx = $('q-context');
  if (q.type === 'timing') {
    const c = q.context || {};
    ctx.innerHTML = `现价 ${safeText(c.current)}；近 1 月 ${fmtPct(c.ret_1m)}；近 3 月 ${fmtPct(c.ret_3m)}；近半年 ${fmtPct(c.ret_6m)}；交易日 ${safeText(c.trade_date)}。`;
    ctx.classList.remove('hidden');
  } else {
    ctx.classList.add('hidden');
    ctx.innerHTML = '';
  }

  const box = $('q-options');
  box.innerHTML = '';
  state.locked = false;
  q.options.forEach(opt => {
    const btn = document.createElement('button');
    btn.className = 'option';
    btn.type = 'button';
    btn.textContent = opt;
    btn.addEventListener('click', () => chooseOption(opt, btn));
    box.appendChild(btn);
  });
}

// 客户端不判定对错（参考答案在结算页揭晓），但答错解锁知识需要本地预判：
// 对业务/热点题，正确答案在 quiz 包里不返回；此处仅“游戏推进时”按概率随机解锁。
function maybeUnlockFact() {
  // 答错才解锁 —— 由于前端不知道对错，改为：每题答完按 35% 概率解锁一条
  // 这样既不剧透答案，也能让“随着游戏推进解锁”成立
  if (Math.random() < 0.35) {
    const f = pickRandomFact();
    if (f) showFact(f);
  }
}

function chooseOption(opt, btn) {
  if (state.locked) return;
  state.locked = true;
  const q = state.quiz.questions[state.index];
  state.answers[String(q.id)] = opt;
  sfx('pick');

  Array.from(document.querySelectorAll('.option')).forEach(b => {
    b.classList.add('disabled');
    b.disabled = true;
  });
  btn.classList.add('picked');

  const advance = () => {
    if (state.index < state.quiz.questions.length - 1) {
      state.index += 1;
      renderQuestion();
    } else {
      $('progress-bar').style.width = '100%';
      submitAnswers();
    }
  };

  // 推进过程：按概率弹出科技知识，关闭后再进入下一题
  setTimeout(() => {
    if (!$('fact-modal').classList.contains('hidden')) {
      // 已在弹出（unlock 内部触发），用户关闭后继续
      const obs = new MutationObserver(() => {
        if ($('fact-modal').classList.contains('hidden')) {
          obs.disconnect();
          advance();
        }
      });
      obs.observe($('fact-modal'), { attributes: true, attributeFilter: ['class'] });
    } else {
      maybeUnlockFact();
      if ($('fact-modal').classList.contains('hidden')) {
        advance();
      } else {
        const obs = new MutationObserver(() => {
          if ($('fact-modal').classList.contains('hidden')) {
            obs.disconnect();
            advance();
          }
        });
        obs.observe($('fact-modal'), { attributes: true, attributeFilter: ['class'] });
      }
    }
  }, 260);
}

async function submitAnswers() {
  try {
    toast('正在结算...');
    const result = await fetchJSON('api/result', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quiz_id: state.quiz.quiz_id, answers: state.answers }),
    });
    sfx('finish');
    renderResult(result);
  } catch (e) {
    toast(e.message || '结算失败');
  }
}

function renderResult(result) {
  showScreen('result');
  $('rank-title').textContent = result.rank;
  $('final-score').textContent = result.total_score;
  $('max-score').textContent = `/ ${result.max_score}`;
  $('result-tip').textContent = rankTip(result.total_score, result.rank);

  const p = result.portfolio || {};
  setRet('ret-1m', p.ret_1m_avg);
  setRet('ret-3m', p.ret_3m_avg);
  setRet('ret-6m', p.ret_6m_avg);
  $('cache-time').textContent = result.updated_at ? `行情缓存更新时间：${result.updated_at}` : '行情缓存时间暂缺';

  renderDetails(p.details || []);
  renderReview(result.questions || []);
}

function rankTip(score, rank) {
  if (score < 100) return `段位：${rank}。科技赛道很刺激，先从公司业务和热点标签练起。`;
  if (score < 200) return `段位：${rank}。你已经能识别不少公司画像，可以继续提升择时判断。`;
  if (score < 300) return `段位：${rank}。基本面和行情都能兼顾，离满分只差一点临门一脚。`;
  return `段位：${rank}。业务、热点、时机三项全能，双创科技雷达已启动。`;
}

function setRet(id, v) {
  const el = $(id);
  el.textContent = fmtPct(v);
  el.className = retClass(v);
}

function renderDetails(details) {
  const box = $('detail-list');
  box.innerHTML = '';
  if (!details.length) {
    box.innerHTML = '<div class="detail-row"><span>暂无行情明细</span></div>';
    return;
  }
  details.forEach(d => {
    const row = document.createElement('div');
    row.className = 'detail-row';
    row.innerHTML = `
      <div>
        <div class="name">${d.name} <span class="meta">${d.code}</span></div>
        <div class="meta">${d.board} · ${d.industry} · 现价 ${safeText(d.current)}</div>
      </div>
      <div class="rets">
        <span class="${retClass(d.ret_1m)}">1m ${fmtPct(d.ret_1m)}</span>
        <span class="${retClass(d.ret_3m)}">3m ${fmtPct(d.ret_3m)}</span>
        <span class="${retClass(d.ret_6m)}">6m ${fmtPct(d.ret_6m)}</span>
      </div>`;
    box.appendChild(row);
  });
}

function renderReview(items) {
  const box = $('review-list');
  box.innerHTML = '';
  items.forEach(it => {
    const row = document.createElement('div');
    row.className = `review-row ${it.is_correct ? 'ok' : 'bad'}`;
    row.innerHTML = `
      <div class="r-h">
        <span class="r-company">${it.company} · ${typeName(it.type)}</span>
        <span class="r-score">${it.score} 分</span>
      </div>
      <div class="r-body">你的答案：<b>${safeText(it.your_answer)}</b>；参考答案：<b>${safeText(it.correct_answer)}</b></div>`;
    box.appendChild(row);
  });
}

function typeName(t) {
  return (TYPE_META[t] && TYPE_META[t].label) || t;
}

function restart() {
  state.quiz = null;
  state.index = 0;
  state.answers = {};
  state.locked = false;
  state.factsShown = new Set();
  showScreen('start');
}

// ==================== 事件绑定 ==================== //
$('btn-start').addEventListener('click', () => { sfx('click'); startGame(); });
$('btn-again').addEventListener('click', () => { sfx('click'); startGame(); });
$('btn-restart').addEventListener('click', () => { sfx('click'); restart(); });
$('btn-fact-close').addEventListener('click', () => { sfx('click'); closeFact(); });
$('btn-sound').addEventListener('click', () => {
  setSound(!state.soundOn);
  sfx('click');
});

// 用户首次任意点击解锁音频（浏览器自动播放策略）
document.addEventListener('pointerdown', () => resumeAudio(), { once: true });
