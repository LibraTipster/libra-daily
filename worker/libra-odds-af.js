// Respaldo del módulo API-Football del Worker libra-odds (Cloudflare). Se añadió al final de worker.js y tres rutas en el router:
//   if (route === "/af/status") return json(await afStatus(env));
//   if (route === "/af/day") return json(await afDay(env, q));
//   if (route === "/af/match") return json(await afMatch(env, q));
// Usa del Worker: UA, nameMatches() y cached(). Secreto requerido: APIFOOTBALL_KEY (no se guarda aquí).

// ---------------------------------------------------------------------------------------------
// API-Football (plan gratis, 100 consultas/día): árbitro, bajas y alineaciones. Secreto: APIFOOTBALL_KEY
//   /<K>/af/status                             plan y consultas usadas hoy (no gasta cupo)
//   /<K>/af/day?date=2026-11-06[&q=premier]    partidos del día (fecha de Lima) con árbitro y bajas (2 consultas, caché 2 h)
//   /<K>/af/match?date=2026-11-06&home=everton&away=coventry
//                                              un partido: árbitro, bajas de ambos equipos y, si ya salieron, alineaciones
// El plan gratis solo deja consultar por fecha (no por liga+temporada): por eso se baja el día entero una vez y se guarda.
// ---------------------------------------------------------------------------------------------
const AF = "https://v3.football.api-sports.io";

async function afGet(env, path) {
  if (!env.APIFOOTBALL_KEY) return { error: "Falta el secreto APIFOOTBALL_KEY en el Worker (Settings > Variables and Secrets, tipo Secret)." };
  const r = await fetch(AF + path, { headers: { "x-apisports-key": env.APIFOOTBALL_KEY, "user-agent": UA } });
  if (env.CACHE) {
    try {
      const k = "afcalls:" + new Date(Date.now() - 5 * 3600 * 1000).toISOString().slice(0, 10);
      const n = Number(await env.CACHE.get(k)) || 0;
      await env.CACHE.put(k, String(n + 1), { expirationTtl: 3 * 86400 });
    } catch (e) { /* contador opcional */ }
  }
  if (!r.ok) return { error: "API-Football HTTP " + r.status };
  const j = await r.json();
  const bad = j.errors && (Array.isArray(j.errors) ? j.errors.length : Object.keys(j.errors).length);
  if (bad) return { error: "API-Football: " + JSON.stringify(j.errors) };
  return { response: j.response || [] };
}

async function afStatus(env) {
  const s = await afGet(env, "/status");
  if (s.error) return s;
  const a = s.response || {};
  let hoy = null;
  if (env.CACHE) {
    try { hoy = Number(await env.CACHE.get("afcalls:" + new Date(Date.now() - 5 * 3600 * 1000).toISOString().slice(0, 10))) || 0; } catch (e) { /* nada */ }
  }
  return { plan: a.subscription && a.subscription.plan, requests: a.requests, callsCountedByWorkerToday: hoy };
}

function afDate(q) {
  const d = (q.get("date") || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return null;
  return d;
}

async function afDayData(env, date) {
  return cached(env, "afday:" + date, 2 * 3600, async () => {
    const fx = await afGet(env, "/fixtures?date=" + date + "&timezone=America/Lima");
    if (fx.error) return fx;
    const inj = await afGet(env, "/injuries?date=" + date + "&timezone=America/Lima");
    if (inj.error) return inj;
    const byFx = new Map();
    for (const x of inj.response) {
      const id = x.fixture && x.fixture.id;
      if (!id) continue;
      if (!byFx.has(id)) byFx.set(id, []);
      byFx.get(id).push({ team: x.team && x.team.name, teamId: x.team && x.team.id, player: x.player && x.player.name, type: x.player && x.player.type, reason: x.player && x.player.reason });
    }
    const fixtures = fx.response.map((f) => ({
      id: f.fixture.id,
      league: f.league.name,
      country: f.league.country,
      home: f.teams.home.name, homeId: f.teams.home.id,
      away: f.teams.away.name, awayId: f.teams.away.id,
      kickoff: f.fixture.date,
      status: f.fixture.status && f.fixture.status.short,
      referee: f.fixture.referee || null,
      injuries: byFx.get(f.fixture.id) || [],
    }));
    return { date, fetchedAt: new Date().toISOString(), fixtures };
  });
}

async function afDay(env, q) {
  const date = afDate(q);
  if (!date) return { error: "Falta date=AAAA-MM-DD (fecha de Lima)" };
  const d = await afDayData(env, date);
  if (d.error) return d;
  const f = (q.get("q") || "").trim();
  const list = f ? d.fixtures.filter((x) => nameMatches(x.league + " " + x.country, f)) : d.fixtures;
  const compact = q.get("compact") !== "0";
  return {
    date, cached: !!d.cached, fetchedAt: d.fetchedAt, count: list.length,
    fixtures: compact ? list.map((x) => ({ id: x.id, league: x.league, country: x.country, home: x.home, away: x.away, kickoff: x.kickoff, referee: x.referee, injuriesCount: x.injuries.length })) : list,
    note: "Las bajas listadas son las que API-Football tiene registradas para ese partido (puede faltar alguna reciente); confirmar con noticias del club si el pick depende de un jugador.",
  };
}

async function afMatch(env, q) {
  const date = afDate(q);
  const home = q.get("home"), away = q.get("away");
  if (!date || !home || !away) return { error: "Faltan date=AAAA-MM-DD, home y away" };
  const d = await afDayData(env, date);
  if (d.error) return d;
  const hit = d.fixtures.find((x) => nameMatches(x.home, home) && nameMatches(x.away, away))
    || d.fixtures.find((x) => nameMatches(x.home, away) && nameMatches(x.away, home));
  if (!hit) return { found: false, date, note: "Partido no encontrado en API-Football para esa fecha (nombres en inglés, fecha de Lima)." };
  const side = (id) => hit.injuries.filter((i) => i.teamId === id).map((i) => ({ player: i.player, type: i.type, reason: i.reason }));
  const out = {
    found: true, date, cached: !!d.cached, fixtureId: hit.id, league: hit.league, country: hit.country,
    home: hit.home, away: hit.away, kickoff: hit.kickoff, status: hit.status, referee: hit.referee,
    injuries: { home: side(hit.homeId), away: side(hit.awayId) },
    lineups: null,
  };
  const mins = (new Date(hit.kickoff).getTime() - Date.now()) / 60000;
  if (hit.status !== "NS" || mins < 150) {
    const lu = await cached(env, "aflu:" + hit.id, 15 * 60, async () => {
      const r = await afGet(env, "/fixtures/lineups?fixture=" + hit.id);
      if (r.error) return r;
      return { teams: r.response.map((t) => ({ team: t.team && t.team.name, formation: t.formation, coach: t.coach && t.coach.name, startXI: (t.startXI || []).map((p) => p.player && p.player.name + (p.player.pos ? " (" + p.player.pos + ")" : "")), subs: (t.substitutes || []).map((p) => p.player && p.player.name) })) };
    });
    if (lu.error) out.lineupsError = lu.error;
    else if (lu.teams && lu.teams.length) out.lineups = lu.teams;
  }
  if (!out.lineups) out.lineupsNote = mins > 150 ? "Faltan más de 2,5 h: aún no se consultan (salen ~1 h antes del partido)." : "Aún no publicadas por API-Football.";
  return out;
}
