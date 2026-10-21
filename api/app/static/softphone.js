/* Browser softphone on top of SIP.js 0.21 (vendor/sip-0.21.2.min.js, global `SIP`).
 *
 * Zero-install WebRTC phone: SIP over WebSocket to Asterisk, DTLS-SRTP media, one call at a time.
 * Ported to plain JavaScript from the Next.js/React example (UserAgent, Registerer, Invitation, Inviter,
 * accept / reject / bye / refer, <audio> element for the remote stream).
 *
 *   const phone = new Softphone({
 *     server: 'ws://localhost:8088/ws', domain: 'localhost', user: '1201', password: '...',
 *     audio: document.getElementById('remote'),
 *     onRegistration: s => ...,          // 'connecting' | 'registered' | 'unregistered' | 'failed' | 'offline'
 *     onCall: c => ...,                  // {state, direction, remote, held, error}
 *   });
 *   await phone.start();
 *
 * Call states: idle | incoming | calling | ringing | talking | hold | ended
 */
class Softphone {
  constructor(opt) {
    this.opt = opt;
    this.ua = null;
    this.registerer = null;
    this.session = null;
    this.call = { state: 'idle', direction: null, remote: '', held: false, error: '' };
    this._stopping = false;
  }

  /* ---------------------------------------------------------- lifecycle */
  async start() {
    const { UserAgent, Registerer, RegistererState } = SIP;
    const o = this.opt;
    this._stopping = false;
    this._reg('connecting');
    this.ua = new UserAgent({
      uri: UserAgent.makeURI(`sip:${o.user}@${o.domain}`),
      transportOptions: { server: o.server },
      authorizationUsername: o.user,
      authorizationPassword: o.password,
      displayName: o.displayName || o.user,
      logLevel: 'warn',
      sessionDescriptionHandlerFactoryOptions: {
        // LAN / Docker only: no STUN/TURN. Asterisk advertises its own address through ICE (ice_host_candidates).
        peerConnectionConfiguration: { iceServers: [] },
        iceGatheringTimeout: 1500,
      },
      delegate: {
        onInvite: inv => this._onInvite(inv),
        onDisconnect: () => this._onDisconnect(),
      },
    });
    await this.ua.start();
    this.registerer = new Registerer(this.ua, { expires: 120 });
    this.registerer.stateChange.addListener(s => {
      if (s === RegistererState.Registered) this._reg('registered');
      else if (s === RegistererState.Unregistered) this._reg('unregistered');
    });
    try {
      await this.registerer.register({
        requestDelegate: { onReject: r => this._reg('failed', `${r.message.statusCode} ${r.message.reasonPhrase}`) },
      });
    } catch (e) {
      this._reg('failed', e.message);
    }
  }

  async stop() {
    this._stopping = true;
    try { if (this.session) await this.hangup(); } catch (e) { /* ignore */ }
    try { if (this.registerer) await this.registerer.unregister(); } catch (e) { /* ignore */ }
    try { if (this.ua) await this.ua.stop(); } catch (e) { /* ignore */ }
    this.ua = this.registerer = null;
    this._reg('offline');
  }

  _reg(state, detail) {
    this.regState = state;
    if (this.opt.onRegistration) this.opt.onRegistration(state, detail || '');
  }

  _onDisconnect() {
    if (this._stopping) return;
    this._reg('connecting', 'reconnecting');
    setTimeout(() => {
      if (this._stopping || !this.ua) return;
      this.ua.reconnect().then(() => this.registerer && this.registerer.register()).catch(() => this._onDisconnect());
    }, 3000);
  }

  /* ---------------------------------------------------------- call state */
  _set(patch) {
    Object.assign(this.call, patch);
    if (this.opt.onCall) this.opt.onCall({ ...this.call });
  }

  _attach(session, direction, remote) {
    const { SessionState } = SIP;
    this.session = session;
    this._set({ state: direction === 'in' ? 'incoming' : 'calling', direction, remote, held: false, error: '' });
    session.stateChange.addListener(s => {
      if (this.session !== session) return;
      if (s === SessionState.Established) {
        this._bindAudio(session);
        this._set({ state: this.call.held ? 'hold' : 'talking' });
      } else if (s === SessionState.Terminated) {
        this.session = null;
        if (this.opt.audio) this.opt.audio.srcObject = null;
        this._set({ state: 'ended', held: false });
        setTimeout(() => { if (!this.session) this._set({ state: 'idle', direction: null, remote: '' }); }, 1500);
      }
    });
  }

  _bindAudio(session) {
    const pc = session.sessionDescriptionHandler && session.sessionDescriptionHandler.peerConnection;
    const el = this.opt.audio;
    if (!pc || !el) return;
    const stream = new MediaStream();
    pc.getReceivers().forEach(r => { if (r.track) stream.addTrack(r.track); });
    el.srcObject = stream;
    const p = el.play();
    if (p && p.catch) p.catch(() => {});
  }

  _onInvite(inv) {
    if (this.session) { inv.reject({ statusCode: 486 }); return; }   // one call at a time -> busy
    const from = (inv.remoteIdentity && inv.remoteIdentity.uri && inv.remoteIdentity.uri.user) || 'unknown';
    this._attach(inv, 'in', from);
  }

  /* ---------------------------------------------------------- actions */
  async dial(target) {
    const { Inviter, UserAgent } = SIP;
    if (this.session) throw new Error('already in a call');
    const uri = UserAgent.makeURI(`sip:${target}@${this.opt.domain}`);
    const inv = new Inviter(this.ua, uri, {
      sessionDescriptionHandlerOptions: { constraints: { audio: true, video: false } },
    });
    this._attach(inv, 'out', target);
    try {
      await inv.invite({
        requestDelegate: {
          onProgress: () => { if (this.session === inv && this.call.state === 'calling') this._set({ state: 'ringing' }); },
          onReject: r => this._set({ error: `${r.message.statusCode} ${r.message.reasonPhrase}` }),
        },
      });
    } catch (e) {
      this._set({ error: e.message });
      this.session = null;
      this._set({ state: 'idle', direction: null, remote: '' });
      throw e;
    }
  }

  async answer() {
    const s = this.session;
    if (!s || this.call.state !== 'incoming') return;
    await s.accept({ sessionDescriptionHandlerOptions: { constraints: { audio: true, video: false } } });
  }

  async reject() {
    const s = this.session;
    if (!s || this.call.state !== 'incoming') return;
    await s.reject({ statusCode: 486, reasonPhrase: 'Busy Here' });
  }

  async hangup() {
    const s = this.session;
    if (!s) return;
    const { SessionState } = SIP;
    switch (s.state) {
      case SessionState.Initial:
      case SessionState.Establishing:
        if (this.call.direction === 'in') await s.reject({ statusCode: 486 }); else await s.cancel();
        break;
      case SessionState.Established:
        await s.bye();
        break;
      default:
    }
  }

  /** DTMF digit (RFC 4733 through RTCDTMFSender). Returns false if there is no established media. */
  dtmf(digit) {
    const s = this.session;
    const sdh = s && s.sessionDescriptionHandler;
    if (!sdh || s.state !== SIP.SessionState.Established) return false;
    return sdh.sendDtmf(String(digit));
  }

  async hold(on) {
    const s = this.session;
    if (!s || s.state !== SIP.SessionState.Established || this.call.held === on) return;
    await s.invite({ sessionDescriptionHandlerModifiers: on ? [SIP.Web.holdModifier] : [] });
    this._set({ held: on, state: on ? 'hold' : 'talking' });
  }

  /** Blind transfer (SIP REFER). target = extension, or a queue name such as "q-motor". */
  async transfer(target) {
    const s = this.session;
    if (!s || s.state !== SIP.SessionState.Established) throw new Error('no established call');
    // RFC 3515: the transferor leaves the call once the REFER is accepted (Asterisk does not BYE it for us)
    await s.refer(SIP.UserAgent.makeURI(`sip:${target}@${this.opt.domain}`), {
      requestDelegate: { onAccept: () => setTimeout(() => { if (this.session === s) s.bye().catch(() => {}); }, 400) },
    });
  }

  mute(on) {
    const s = this.session;
    const pc = s && s.sessionDescriptionHandler && s.sessionDescriptionHandler.peerConnection;
    if (pc) pc.getSenders().forEach(x => { if (x.track) x.track.enabled = !on; });
  }
}
window.Softphone = Softphone;

/* Local tones: DTMF keypad beeps and incoming-call ringtone (WebAudio). */
const Tones = (() => {
  let ctx = null, ringT = null;
  const ac = () => {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  };
  const beep = (freqs, dur, vol) => {
    const c = ac(), g = c.createGain(), t = c.currentTime;
    g.gain.setValueAtTime(vol, t); g.gain.setValueAtTime(vol, t + dur - 0.01); g.gain.linearRampToValueAtTime(0, t + dur);
    g.connect(c.destination);
    for (const f of freqs) { const o = c.createOscillator(); o.frequency.value = f; o.connect(g); o.start(t); o.stop(t + dur); }
  };
  const ROW = { 1: 697, 2: 697, 3: 697, 4: 770, 5: 770, 6: 770, 7: 852, 8: 852, 9: 852, '*': 941, 0: 941, '#': 941 };
  const COL = { 1: 1209, 2: 1336, 3: 1477, 4: 1209, 5: 1336, 6: 1477, 7: 1209, 8: 1336, 9: 1477, '*': 1209, 0: 1336, '#': 1477 };
  return {
    key(k) { if (ROW[k]) { try { beep([ROW[k], COL[k]], 0.12, 0.08); } catch (e) { /* no audio */ } } },
    ring(on) {
      if (on && !ringT) {
        const burst = () => { try { beep([440, 480], 0.9, 0.08); } catch (e) { /* no audio */ } };
        burst(); ringT = setInterval(burst, 3000);
      } else if (!on && ringT) { clearInterval(ringT); ringT = null; }
    },
  };
})();
window.Tones = Tones;
