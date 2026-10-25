/* Ponte dos joguinhos com a fazenda.
 * Dentro da fazenda (iframe) quem guarda placar e moedas é a fazenda: o jogo avisa o fim da partida
 * e recebe de volta os totais. Aberto sozinho, guarda tudo no localStorage deste navegador.
 *
 *   FZ.init('cobrinha', st => ...)   st = { melhor: recorde deste jogo, moedas: total de todos os jogos, partidas }
 *   FZ.result(pontos, moedas)        fim da partida (chame uma vez); os totais voltam pelo callback do init
 */
(function () {
  var GAMES = ['cobrinha', 'toupeira', 'memoria', 'galinha'];
  var inFarm = false;
  try { inFarm = window.parent !== window; } catch (e) {}
  var FZ = window.FZ = { inFarm: inFarm, game: '', onstats: function () {} };

  function post(m) {
    m.fz = 1;
    try { window.parent.postMessage(m, location.origin); } catch (e) {}
  }

  function load(g) {
    try {
      var j = JSON.parse(localStorage.getItem('fz-jogo-' + g) || '{}');
      return { melhor: j.melhor | 0, partidas: j.partidas | 0, moedas: j.moedas | 0 };
    } catch (e) { return { melhor: 0, partidas: 0, moedas: 0 }; }
  }

  function localStats() {
    var me = load(FZ.game), total = 0;
    GAMES.forEach(function (g) { total += load(g).moedas; });
    return { melhor: me.melhor, partidas: me.partidas, moedas: total };
  }

  FZ.init = function (game, cb) {
    FZ.game = game;
    if (cb) FZ.onstats = cb;
    if (inFarm) post({ type: 'ready', game: game });
    else FZ.onstats(localStats());
  };

  FZ.result = function (points, coins) {
    points = Math.max(0, points | 0);
    coins = Math.max(0, coins | 0);
    if (inFarm) { post({ type: 'result', game: FZ.game, points: points, coins: coins }); return; }
    var me = load(FZ.game);
    me.melhor = Math.max(me.melhor, points);
    me.partidas += 1;
    me.moedas += coins;
    try { localStorage.setItem('fz-jogo-' + FZ.game, JSON.stringify(me)); } catch (e) {}
    FZ.onstats(localStats());
  };

  window.addEventListener('message', function (e) {
    var m = e.data;
    if (e.origin !== location.origin || e.source !== window.parent || !m || m.fz !== 1) return;
    if (m.type === 'stats' && m.game === FZ.game && m.stats) FZ.onstats(m.stats);
  });

  // Esc volta para a fazenda
  window.addEventListener('keydown', function (e) {
    if (inFarm && e.key === 'Escape') post({ type: 'close' });
  });
  try { window.focus(); } catch (e) {}
})();
