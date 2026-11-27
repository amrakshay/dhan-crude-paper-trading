/**
 * Strategy templates for the Options Trader basket (OPTIONS_TRADER_PLAN.md
 * Phase 5). A template only BUILDS legs from the chain on screen -- it never
 * places anything: the legs go into the basket, where the SPAN preview, the
 * payoff and the confirm step apply exactly as for legs added by hand.
 *
 * Offsets are in strikes from the ATM strike, scaled by the chosen width:
 * `+1` is one width above ATM. Calls above ATM and puts below are OTM.
 */
export const TEMPLATES = [
  { key: 'bull-call-spread', label: 'Bull call spread', legs: [['CE', 'BUY', 0], ['CE', 'SELL', 1]] },
  { key: 'bear-put-spread', label: 'Bear put spread', legs: [['PE', 'BUY', 0], ['PE', 'SELL', -1]] },
  { key: 'bull-put-spread', label: 'Bull put spread (credit)', legs: [['PE', 'SELL', 0], ['PE', 'BUY', -1]] },
  { key: 'bear-call-spread', label: 'Bear call spread (credit)', legs: [['CE', 'SELL', 0], ['CE', 'BUY', 1]] },
  { key: 'long-straddle', label: 'Long straddle', legs: [['CE', 'BUY', 0], ['PE', 'BUY', 0]] },
  { key: 'short-straddle', label: 'Short straddle', legs: [['CE', 'SELL', 0], ['PE', 'SELL', 0]] },
  { key: 'long-strangle', label: 'Long strangle', legs: [['CE', 'BUY', 1], ['PE', 'BUY', -1]] },
  { key: 'short-strangle', label: 'Short strangle', legs: [['CE', 'SELL', 1], ['PE', 'SELL', -1]] },
  {
    key: 'iron-condor',
    label: 'Iron condor',
    legs: [['CE', 'SELL', 1], ['CE', 'BUY', 2], ['PE', 'SELL', -1], ['PE', 'BUY', -2]],
  },
  {
    key: 'iron-butterfly',
    label: 'Iron butterfly',
    legs: [['CE', 'SELL', 0], ['PE', 'SELL', 0], ['CE', 'BUY', 1], ['PE', 'BUY', -1]],
  },
];

/**
 * Pick the contracts for a template from the chain.
 * Returns { picks: [{ contract, row, optionType, side }], missing: string|null }.
 */
export function pickTemplate(template, chain, width) {
  const rows = [...(chain?.rows ?? [])].sort((a, b) => a.strike - b.strike);
  const atmIndex = rows.findIndex((row) => row.strike === chain?.atmStrike);
  if (atmIndex < 0) return { picks: [], missing: 'The chain has no ATM strike yet.' };
  const picks = [];
  for (const [optionType, side, offset] of template.legs) {
    const row = rows[atmIndex + offset * width];
    const contract = row ? (optionType === 'CE' ? row.call : row.put) : null;
    if (!contract) {
      return {
        picks: [],
        missing: `No ${optionType} ${offset * width >= 0 ? '+' : ''}${offset * width} strikes from ATM in this chain; try a smaller width.`,
      };
    }
    picks.push({ contract, row, optionType, side });
  }
  return { picks, missing: null };
}
