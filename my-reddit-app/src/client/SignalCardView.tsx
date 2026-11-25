import React, { useEffect, useMemo, useState } from 'react';
import { context, exitExpandedMode, getWebViewMode, requestExpandedMode } from '@devvit/web/client';
import type { SignalCardDetail, SignalCardPostData } from '../shared/types.js';

function fmtPx(value: number, decimals: number): string {
  if (!Number.isFinite(value)) return '—';
  const dec = Math.max(0, Math.min(6, Number.isFinite(decimals) ? decimals : 2));
  return value.toLocaleString('en-US', {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
}

function fmtSigned(value: number, decimals: number): string {
  if (!Number.isFinite(value)) return '—';
  return `${value >= 0 ? '+' : ''}${fmtPx(value, decimals)}`;
}

function statusMeta(card: SignalCardPostData): { label: string; cls: string } {
  const st = (card.status || 'ACTIVE').toUpperCase();
  const rSuffix = card.rLabel ? ` ${card.rLabel}` : '';
  if (st === 'ACTIVE') return { label: '● ACTIVE', cls: 'st-active' };
  if (st === 'TP1') return { label: `🎯 TP1${rSuffix}`, cls: 'st-win' };
  if (st === 'TP2') return { label: `🥈 TP2${rSuffix}`, cls: 'st-win' };
  if (st === 'TP3') return { label: `👑 TP3${rSuffix}`, cls: 'st-win' };
  if (st.includes('TRAIL')) return { label: `📈 TRAIL${rSuffix}`, cls: 'st-win' };
  if (st === 'BE' || st.includes('BREAKEVEN')) return { label: `🛡️ BE${rSuffix}`, cls: 'st-neutral' };
  if (st === 'SL' || st.includes('STOP LOSS')) return { label: `🛑 SL${rSuffix}`, cls: 'st-loss' };
  if (st.includes('EARLY')) return { label: `⚠️ EXIT${rSuffix}`, cls: 'st-warn' };
  if (st.includes('TIME')) return { label: `⏱️ EXPIRY${rSuffix}`, cls: 'st-neutral' };
  return { label: `${st}${rSuffix}`, cls: card.closed ? 'st-neutral' : 'st-active' };
}

function readInitialView(): 'inline' | 'detail' | 'calc' {
  const rootView = document.getElementById('root')?.getAttribute('data-view');
  if (rootView === 'detail' || rootView === 'calc') return rootView;
  const params = new URLSearchParams(window.location.search);
  const v = params.get('view');
  if (v === 'detail') return 'detail';
  if (v === 'calc') return 'calc';
  try {
    if (getWebViewMode() === 'expanded') return 'detail';
  } catch {
    // ignore outside devvit iframe
  }
  return 'inline';
}

export function SignalCardView(): React.JSX.Element {
  const postData = context.postData as SignalCardPostData | undefined;
  const initialCard = postData && postData.kind === 'signal' ? postData : null;

  const [view, setView] = useState<'inline' | 'detail' | 'calc'>(() => readInitialView());
  const [detail, setDetail] = useState<SignalCardDetail | null>(() =>
    initialCard
      ? {
          card: initialCard,
          regimeSummary: `${initialCard.regime} · ${initialCard.trigger}`,
          validUntil: '',
          riskFields: [],
          mtfRows: [],
          confluenceSummary: initialCard.confluence || '',
          confluencePassed: [],
          confluenceMissed: [],
          marketData: [],
          whySetup: [],
          warnings: [],
          trackRecordTitle: 'Walk-Forward Track Record',
          trackRecordSummary: initialCard.edge,
          events: [],
        }
      : null
  );
  const [loadingDetail, setLoadingDetail] = useState<boolean>(!initialCard);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Local instant position calculator state
  const [equityInput, setEquityInput] = useState<string>('1000');
  const [riskPctInput, setRiskPctInput] = useState<string>('1.0');
  const [journalMsg, setJournalMsg] = useState<string | null>(null);
  const [savingJournal, setSavingJournal] = useState<boolean>(false);

  useEffect(() => {
    let active = true;
    fetch('/api/signal-post')
      .then(async (resp) => {
        if (!resp.ok) return null;
        return (await resp.json()) as { detail?: SignalCardDetail };
      })
      .then((payload) => {
        if (!active) return;
        if (payload?.detail) {
          setDetail(payload.detail);
        } else if (!initialCard) {
          setErrorMsg('Signal details are syncing — refresh in a moment.');
        }
      })
      .catch(() => {
        if (active && !initialCard) setErrorMsg('Could not load signal data.');
      })
      .finally(() => {
        if (active) setLoadingDetail(false);
      });
    return () => {
      active = false;
    };
  }, [initialCard]);

  const card = detail?.card ?? initialCard;

  const calc = useMemo(() => {
    if (!card) return null;
    const equity = Math.max(1, Number(equityInput) || 1000);
    const baseRiskPct = Math.max(0.05, Math.min(25, Number(riskPctInput) || 1.0));
    const sizeMult = Math.max(0.1, Math.min(2, card.sizeMult || 1.0));
    const effRiskPct = baseRiskPct * sizeMult;
    const riskAmount = equity * (effRiskPct / 100);
    const stopDist = Math.abs(card.entry - card.sl);
    if (stopDist <= 1e-12) return null;
    const units = riskAmount / stopDist;
    const pipVal = card.pipValue > 0 ? card.pipValue : 0.01;
    return {
      equity,
      baseRiskPct,
      sizeMult,
      effRiskPct,
      riskAmount,
      stopDist,
      stopPips: stopDist / pipVal,
      units,
      tp1Profit: Math.abs(card.tp1 - card.entry) * units,
      tp2Profit: Math.abs(card.tp2 - card.entry) * units,
      tp3Profit: Math.abs(card.tp3 - card.entry) * units,
    };
  }, [card, equityInput, riskPctInput]);

  const openExpanded = (e: React.MouseEvent<HTMLButtonElement>, target: 'detail' | 'calc') => {
    const entryName = target === 'calc' ? 'signal-calc' : 'signal-detail';
    try {
      if (getWebViewMode() !== 'expanded') {
        requestExpandedMode(e.nativeEvent, entryName);
        return;
      }
    } catch {
      // Fallback to in-place switch if expanded mode isn't supported in this client
    }
    setView(target);
  };

  const closeExpanded = (e: React.MouseEvent<HTMLButtonElement>) => {
    try {
      if (getWebViewMode() === 'expanded') {
        exitExpandedMode(e.nativeEvent);
        return;
      }
    } catch {
      // ignore
    }
    setView('inline');
  };

  const saveJournalEntry = async () => {
    if (!card || !calc) return;
    setSavingJournal(true);
    setJournalMsg(null);
    try {
      const resp = await fetch('/api/journal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          signal: {
            symbol: card.sym,
            name: card.name,
            direction: card.dir,
            grade: card.grade,
            plan: {
              entry: card.entry,
              sl: card.sl,
              tp1: card.tp1,
              tp2: card.tp2,
              tp3: card.tp3,
            },
          },
          equity: calc.equity,
          riskPct: Number(calc.effRiskPct.toFixed(2)),
          units: Number(calc.units.toFixed(4)),
          riskAmount: Number(calc.riskAmount.toFixed(2)),
          note: `${card.dir} ${card.sym} (${card.grade}) from #${card.ch}`,
        }),
      });
      if (resp.ok) {
        setJournalMsg('✅ Saved entry & sizing to your personal trade journal!');
      } else {
        const d = (await resp.json().catch(() => ({}))) as { error?: string };
        setJournalMsg(`⚠️ ${d.error ?? 'Sign in to Reddit to save journal entries.'}`);
      }
    } catch {
      setJournalMsg('⚠️ Could not save right now.');
    } finally {
      setSavingJournal(false);
    }
  };

  if (loadingDetail && !card) {
    return (
      <div className="sc-shell sc-inline">
        <div className="sc-loading">Loading structured signal summary…</div>
      </div>
    );
  }

  if (!card) {
    return (
      <div className="sc-shell sc-inline">
        <div className="sc-loading">{errorMsg ?? 'Signal summary unavailable.'}</div>
      </div>
    );
  }

  const isLong = card.dir === 'LONG';
  const st = statusMeta(card);
  const slDelta = card.sl - card.entry;
  const hasLimit = Math.abs(card.limit - card.entry) > 1e-9;

  // ---------- COMPACT FEED SUMMARY CARD (INLINE IN r/GlvnsOrg FEED) ----------
  if (view === 'inline') {
    return (
      <div className={`sc-shell sc-inline ${isLong ? 'is-long' : 'is-short'}`}>
        <div className="sc-header">
          <div className="sc-title-group">
            <span className={`sc-dir-pill ${isLong ? 'long' : 'short'}`}>
              {isLong ? '▲ LONG' : '▼ SHORT'}
            </span>
            <span className="sc-name">{card.name}</span>
            <span className="sc-sym">{card.sym}</span>
          </div>
          <div className="sc-badge-group">
            <span className="sc-grade-pill">
              Grade {card.grade} · {card.score}/100
            </span>
            <span className={`sc-status-pill ${st.cls}`}>{st.label}</span>
          </div>
        </div>

        <div className="sc-subbar">
          <span
            className="sc-ch-pill"
            style={{ borderColor: `${card.chColor}88`, background: `${card.chColor}24` }}
          >
            {card.chLabel}
          </span>
          <span className="sc-meta-sep">•</span>
          <span className="sc-trigger">{card.trigger}</span>
          <span className="sc-meta-sep">•</span>
          <span className="sc-meta-item">{card.tf}</span>
          <span className="sc-meta-sep">•</span>
          <span className="sc-meta-item">{card.timeUtc}</span>
          <span className="sc-meta-sep">•</span>
          <span className={`sc-edge-tag ${card.edge.includes('Proven') ? 'proven' : ''}`}>
            {card.edge}
          </span>
        </div>

        <div className="sc-plan-grid">
          <div className="sc-tile tile-entry">
            <div className="sc-tile-lbl">ENTRY (MKT)</div>
            <div className="sc-tile-val">{fmtPx(card.entry, card.dec)}</div>
            <div className="sc-tile-sub">
              {hasLimit ? `Limit ${fmtPx(card.limit, card.dec)}` : 'Spot bar close'}
            </div>
          </div>

          <div className="sc-tile tile-sl">
            <div className="sc-tile-lbl">STOP LOSS</div>
            <div className="sc-tile-val">{fmtPx(card.sl, card.dec)}</div>
            <div className="sc-tile-sub">
              {fmtSigned(slDelta, card.dec)}
              {card.atrMult ? ` · ${card.atrMult}` : ''}
            </div>
          </div>

          <div className="sc-tile tile-tp">
            <div className="sc-tile-lbl">TP1 · {card.r1.toFixed(2)}R</div>
            <div className="sc-tile-val">{fmtPx(card.tp1, card.dec)}</div>
            <div className="sc-tile-sub">Bank {card.tp1Bank}% · SL→BE</div>
          </div>

          <div className="sc-tile tile-tp">
            <div className="sc-tile-lbl">TP2 · {card.r2.toFixed(2)}R</div>
            <div className="sc-tile-val">{fmtPx(card.tp2, card.dec)}</div>
            <div className="sc-tile-sub">Bank {card.tp2Bank}% · SL→TP1</div>
          </div>

          <div className="sc-tile tile-tp3">
            <div className="sc-tile-lbl">TP3 · {card.r3.toFixed(2)}R</div>
            <div className="sc-tile-val">{fmtPx(card.tp3, card.dec)}</div>
            <div className="sc-tile-sub">Trail {card.tp3Bank}% runner</div>
          </div>
        </div>

        <div className="sc-info-strip">
          <div className="sc-info-cell">
            <span className="sc-info-k">Regime</span>
            <span className="sc-info-v">{card.regime}</span>
          </div>
          <div className="sc-info-cell">
            <span className="sc-info-k">Risk &amp; Horizon</span>
            <span className="sc-info-v">
              ≤ {card.sizeMult.toFixed(2)}% eq · {card.riskPct.toFixed(2)}% stop · {card.horizon || '480m'}
            </span>
          </div>
          <div className="sc-info-cell">
            <span className="sc-info-k">Confluence</span>
            <span className="sc-info-v">{card.confluence || card.session}</span>
          </div>
        </div>

        <div className="sc-footer">
          <div className="sc-btn-group">
            <button className="sc-btn primary" onClick={(e) => openExpanded(e, 'detail')}>
              📋 Full Breakdown
            </button>
            <button className="sc-btn" onClick={(e) => openExpanded(e, 'calc')}>
              🧮 Size Calc
            </button>
          </div>
          <span className="sc-foot-hint">💬 Live TP/SL updates in comments</span>
        </div>
      </div>
    );
  }

  // ---------- EXPANDED STRUCTURED MODAL (BREAKDOWN + SIZE CALCULATOR) ----------
  return (
    <div className={`sc-shell sc-expanded ${isLong ? 'is-long' : 'is-short'}`}>
      <div className="sc-exp-topbar">
        <div className="sc-title-group">
          <span className={`sc-dir-pill ${isLong ? 'long' : 'short'}`}>
            {isLong ? '▲ LONG' : '▼ SHORT'}
          </span>
          <span className="sc-name">{card.name}</span>
          <span className="sc-sym">{card.sym}</span>
          <span className="sc-grade-pill">
            Grade {card.grade} · {card.score}/100
          </span>
          <span className={`sc-status-pill ${st.cls}`}>{st.label}</span>
        </div>
        <button className="sc-btn" onClick={closeExpanded}>
          ✕ Close
        </button>
      </div>

      <div className="sc-exp-tabs">
        <button
          className={`sc-tab ${view === 'detail' ? 'active' : ''}`}
          onClick={() => setView('detail')}
        >
          📋 Full Signal Breakdown
        </button>
        <button
          className={`sc-tab ${view === 'calc' ? 'active' : ''}`}
          onClick={() => setView('calc')}
        >
          🧮 Position Size Calculator
        </button>
      </div>

      <div className="sc-exp-body">
        {view === 'detail' && (
          <>
            <div className="sc-plan-grid">
              <div className="sc-tile tile-entry">
                <div className="sc-tile-lbl">ENTRY (MARKET)</div>
                <div className="sc-tile-val">{fmtPx(card.entry, card.dec)}</div>
                <div className="sc-tile-sub">
                  {hasLimit
                    ? `Limit ${fmtPx(card.limit, card.dec)}${detail?.validUntil ? ` · ${detail.validUntil}` : ''}`
                    : 'Spot bar close'}
                </div>
              </div>
              <div className="sc-tile tile-sl">
                <div className="sc-tile-lbl">STOP LOSS (1R)</div>
                <div className="sc-tile-val">{fmtPx(card.sl, card.dec)}</div>
                <div className="sc-tile-sub">
                  {fmtSigned(slDelta, card.dec)}
                  {card.atrMult ? ` · ${card.atrMult}` : ''}
                </div>
              </div>
              <div className="sc-tile tile-tp">
                <div className="sc-tile-lbl">TP1 · {card.r1.toFixed(2)}R</div>
                <div className="sc-tile-val">{fmtPx(card.tp1, card.dec)}</div>
                <div className="sc-tile-sub">Bank {card.tp1Bank}% · SL→BE</div>
              </div>
              <div className="sc-tile tile-tp">
                <div className="sc-tile-lbl">TP2 · {card.r2.toFixed(2)}R</div>
                <div className="sc-tile-val">{fmtPx(card.tp2, card.dec)}</div>
                <div className="sc-tile-sub">Bank {card.tp2Bank}% · SL→TP1</div>
              </div>
              <div className="sc-tile tile-tp3">
                <div className="sc-tile-lbl">TP3 · {card.r3.toFixed(2)}R</div>
                <div className="sc-tile-val">{fmtPx(card.tp3, card.dec)}</div>
                <div className="sc-tile-sub">Trail {card.tp3Bank}% runner</div>
              </div>
            </div>

            {detail && detail.events.length > 0 && (
              <div className="sc-section">
                <div className="sc-sec-title">📡 Live Trade Lifecycle Updates ({detail.events.length})</div>
                <div className="sc-events-list">
                  {detail.events.map((ev) => (
                    <div key={ev.ts} className="sc-event-row">
                      <span className="sc-event-tag">{ev.kindTag}</span>
                      <div className="sc-event-text">
                        <strong>{ev.title}</strong>
                        {ev.rLabel ? ` (${ev.rLabel})` : ''}
                        <div className="sc-muted">{ev.summary}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {detail?.regimeSummary && (
              <div className="sc-section sc-thesis">
                <div className="sc-sec-title">🎯 Setup Thesis · {card.trigger}</div>
                <div>{detail.regimeSummary}</div>
              </div>
            )}

            <div className="sc-two-col">
              {detail && detail.mtfRows.length > 0 && (
                <div className="sc-section">
                  <div className="sc-sec-title">🧭 Multi-Timeframe Alignment</div>
                  <div className="sc-list-rows">
                    {detail.mtfRows.map((row, i) => (
                      <div key={i} className="sc-mtf-row">
                        <div>
                          <strong>{row.label}</strong>
                          <div className="sc-muted">{row.detail}</div>
                        </div>
                        <span className={`sc-mini-pill ${row.aligned ? 'ok' : 'warn'}`}>
                          {row.aligned ? '✅ Aligned' : '⚠️ Opposing'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="sc-section">
                <div className="sc-sec-title">⚖️ Risk &amp; Execution Rules</div>
                <div className="sc-kv-grid">
                  {(detail?.riskFields.length
                    ? detail.riskFields
                    : [
                        { label: 'Equity at Risk', value: `≤ ${card.sizeMult.toFixed(2)}%` },
                        { label: 'Stop Distance', value: `${card.riskPct.toFixed(2)}%` },
                        { label: 'Reward:Risk', value: `${card.r1} / ${card.r2} / ${card.r3}` },
                        { label: 'Horizon', value: card.horizon || '480m' },
                      ]
                  ).map((f, i) => (
                    <div key={i} className="sc-kv-item">
                      <span className="sc-kv-k">{f.label}</span>
                      <span className="sc-kv-v">{f.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {detail && (detail.confluencePassed.length > 0 || detail.confluenceMissed.length > 0) && (
              <div className="sc-section">
                <div className="sc-sec-title">
                  ✅ {detail.confluenceSummary || `Confluence (${card.confluence})`}
                </div>
                <div className="sc-chip-wrap">
                  {detail.confluencePassed.map((c, i) => (
                    <span key={`p-${i}`} className="sc-chip pass">
                      ✓ {c}
                    </span>
                  ))}
                  {detail.confluenceMissed.map((c, i) => (
                    <span key={`m-${i}`} className="sc-chip miss">
                      ✗ {c}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {detail && detail.whySetup.length > 0 && (
              <div className="sc-section">
                <div className="sc-sec-title">💡 Why This Setup Triggered</div>
                <ul className="sc-bullets">
                  {detail.whySetup.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {detail && detail.warnings.length > 0 && (
              <div className="sc-section sc-warn-box">
                <div className="sc-sec-title">⚠️ Risk Watch-Outs</div>
                <ul className="sc-bullets">
                  {detail.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {detail && detail.marketData.length > 0 && (
              <div className="sc-section">
                <div className="sc-sec-title">📊 Market Structure Snapshot</div>
                <div className="sc-kv-grid sc-kv-4">
                  {detail.marketData.map((m, i) => (
                    <div key={i} className="sc-kv-item">
                      <span className="sc-kv-k">{m.label}</span>
                      <span className="sc-kv-v">{m.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {detail?.trackRecordSummary && (
              <div className="sc-section">
                <div className="sc-sec-title">🧪 {detail.trackRecordTitle}</div>
                <div className="sc-muted">{detail.trackRecordSummary}</div>
              </div>
            )}
          </>
        )}

        {view === 'calc' && (
          <div className="sc-section">
            <div className="sc-sec-title">
              🧮 Instant Position Sizing · {card.dir} {card.name} ({card.sym})
            </div>
            <div className="sc-muted" style={{ marginBottom: 10 }}>
              Uses this signal&apos;s exact Entry/SL/TP geometry with regime scale{' '}
              <strong>×{card.sizeMult.toFixed(2)}</strong>.
            </div>

            <div className="sc-calc-inputs">
              <label className="sc-field">
                <span>Account Equity ($)</span>
                <input
                  type="number"
                  min="1"
                  value={equityInput}
                  onChange={(e) => setEquityInput(e.target.value)}
                />
              </label>
              <label className="sc-field">
                <span>Base Risk (%)</span>
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="10"
                  value={riskPctInput}
                  onChange={(e) => setRiskPctInput(e.target.value)}
                />
              </label>
              <div className="sc-presets">
                {['500', '1000', '2500', '5000'].map((eq) => (
                  <button key={eq} className="sc-btn" onClick={() => setEquityInput(eq)}>
                    ${eq}
                  </button>
                ))}
                {['0.5', '1.0', '2.0'].map((rp) => (
                  <button key={rp} className="sc-btn" onClick={() => setRiskPctInput(rp)}>
                    {rp}%
                  </button>
                ))}
              </div>
            </div>

            {calc && (
              <>
                <div className="sc-kv-grid sc-kv-4" style={{ marginTop: 12 }}>
                  <div className="sc-kv-item">
                    <span className="sc-kv-k">Dollar Risk (1R)</span>
                    <span className="sc-kv-v sc-big">${calc.riskAmount.toFixed(2)}</span>
                    <span className="sc-muted">Eff. {calc.effRiskPct.toFixed(2)}%</span>
                  </div>
                  <div className="sc-kv-item">
                    <span className="sc-kv-k">Position Size</span>
                    <span className="sc-kv-v sc-big">{calc.units.toFixed(4)} u</span>
                    <span className="sc-muted">
                      Stop {calc.stopPips.toFixed(1)} {card.pipUnit}
                    </span>
                  </div>
                  <div className="sc-kv-item">
                    <span className="sc-kv-k">TP1 / TP2 Profit</span>
                    <span className="sc-kv-v sc-big sc-green">
                      +${calc.tp1Profit.toFixed(2)} / +${calc.tp2Profit.toFixed(2)}
                    </span>
                    <span className="sc-muted">
                      {card.r1.toFixed(2)}R / {card.r2.toFixed(2)}R
                    </span>
                  </div>
                  <div className="sc-kv-item">
                    <span className="sc-kv-k">TP3 Full Target</span>
                    <span className="sc-kv-v sc-big sc-gold">+${calc.tp3Profit.toFixed(2)}</span>
                    <span className="sc-muted">{card.r3.toFixed(2)}R runner</span>
                  </div>
                </div>

                <div className="sc-btn-group" style={{ marginTop: 14 }}>
                  <button
                    className="sc-btn success"
                    disabled={savingJournal}
                    onClick={() => void saveJournalEntry()}
                  >
                    {savingJournal ? 'Saving…' : '✅ Log Entry to My Journal'}
                  </button>
                  <button className="sc-btn" onClick={() => setView('detail')}>
                    📋 Back to Breakdown
                  </button>
                </div>
                {journalMsg && (
                  <div className="sc-journal-note" style={{ marginTop: 8 }}>
                    {journalMsg}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
