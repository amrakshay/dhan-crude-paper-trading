/**
 * Design tokens ported from the Privacera SaaS portal theme.
 *
 * Source:
 *   privacera-saas-portal/src/main/webapp/app/scripts/components/site/theme.js
 *   privacera-saas-portal/src/main/webapp/app/scripts/components/site/theme_dark.js
 *
 * That portal is on MUI v4.11.4, which is end of life. These are the extracted
 * palette / typography / spacing / shape values only; they are re-expressed for
 * a current MUI createTheme in ./index.js. None of the portal's MobX or webpack
 * tooling is carried over.
 *
 * The dark values, including the WCAG contrast ratios noted in comments, come
 * from the DARK_COLORS block at the top of theme_dark.js.
 */

export const light = {
  brand: '#354bbb',
  brandHover: '#2438b8',
  brandSoft: '#E6E9FB',       // selected list row
  brandTint: '#354bbb0A',     // text/outlined button hover
  brandDisabled: '#354bbb4D',
  secondary: '#FC5439',
  error: '#E10101',
  textPrimary: '#172538',
  textSecondary: '#41546F',
  base: '#F5F7FB',            // page background
  surface: '#FFFFFF',         // cards, tables, drawer
  border: '#D8DBDE',
  borderSubtle: '#E0E0E0',
  neutralHover: '#F5F5F5',    // row hover, table head
  chip: '#EBEDF1',
  tooltip: '#424242',
  muted: '#797C80',
  appBarShadow: '0 8px 40px 0 rgba(0, 0, 0, 0.08)',
};

export const dark = {
  brand: '#354bbb',
  brandHover: '#2D3FA3',
  link: '#afbdf8',
  brandSoft: 'rgba(53, 75, 187, 0.16)',
  brandTint: 'rgba(53, 75, 187, 0.08)',
  secondary: '#FC5439',
  error: '#FF8A7A',           // AA 5.1:1
  errorContained: '#B84334',  // darker so white label clears AAA 7:1
  success: '#81C784',         // AA 6.2:1
  warning: '#FFB74D',
  textPrimary: '#F0F2F5',     // AAA 12:1
  textSecondary: '#B0B8C8',   // AA 7:1
  base: '#1A1D24',
  surface: '#212730',
  header: '#303848',          // table headers
  border: '#6B7280',          // visible UI borders, 3:1+ on surface
  neutralHover: '#252C3A',
  chip: '#303848',
  tooltip: '#424242',
};

/** Shared, mode-independent tokens. */
export const shared = {
  fontFamily: "'Inter', 'Roboto', 'Arial', sans-serif",
  monoFontFamily: "'Roboto Mono', 'SFMono-Regular', Menlo, Consolas, monospace",
  borderRadius: 8,
  spacing: 8,
  contentPadding: 24,
  appBarHeight: 57,
  sidebarWidth: 232,
};

/**
 * Market semantics. The portal has no up/down colours, so these are added here
 * and tuned to sit alongside its palette rather than fight it.
 */
export const market = {
  light: {
    up: '#0B8A5B',
    down: '#D13438',
    upSoft: 'rgba(11, 138, 91, 0.10)',
    downSoft: 'rgba(209, 52, 56, 0.10)',
    // Volume histogram bars. Heavier than *Soft, which is a row tint and
    // disappears entirely when drawn as a one-pixel-wide bar.
    upVolume: 'rgba(11, 138, 91, 0.45)',
    downVolume: 'rgba(209, 52, 56, 0.45)',
    atm: 'rgba(53, 75, 187, 0.10)',
    itm: 'rgba(255, 196, 0, 0.09)',
  },
  dark: {
    up: '#5ED6A0',
    down: '#FF8A7A',
    upSoft: 'rgba(94, 214, 160, 0.12)',
    downSoft: 'rgba(255, 138, 122, 0.12)',
    upVolume: 'rgba(94, 214, 160, 0.45)',
    downVolume: 'rgba(255, 138, 122, 0.45)',
    atm: 'rgba(53, 75, 187, 0.28)',
    itm: 'rgba(255, 196, 0, 0.10)',
  },
};
