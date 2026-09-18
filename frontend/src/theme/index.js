/**
 * The Privacera portal's visual identity, re-expressed for MUI v6.
 *
 * The portal itself is on MUI v4 (`overrides` + `props`, JSS). Everything here
 * is the v5+ equivalent (`components.styleOverrides`, emotion). The identity to
 * preserve: flat surfaces with borders instead of shadows, 8px radius
 * everywhere, Inter, indigo #354bbb as the single accent, and generous table
 * padding.
 */
import { createTheme } from '@mui/material/styles';
import { dark, light, market, shared } from './tokens';

const typography = {
  fontFamily: shared.fontFamily,
  h1: { fontSize: '1.75rem', fontWeight: 600, letterSpacing: '-0.01em' },
  h2: { fontSize: '1.5rem', fontWeight: 600, letterSpacing: '-0.01em' },
  h3: { fontSize: '1.25rem', fontWeight: 600 },
  h4: { fontSize: '1.125rem', fontWeight: 600 },
  h5: { fontSize: '1rem', fontWeight: 600 },
  h6: { fontSize: '0.9375rem', fontWeight: 600 },
  subtitle1: { fontSize: '0.9375rem', fontWeight: 500 },
  subtitle2: { fontSize: '0.8125rem', fontWeight: 500 },
  body1: { fontSize: '0.875rem' },
  body2: { fontSize: '0.8125rem' },
  caption: { fontSize: '0.75rem' },
  button: { textTransform: 'none', fontWeight: 600 },
};

function buildPalette(mode) {
  if (mode === 'dark') {
    return {
      mode: 'dark',
      primary: {
        light: dark.brand,
        main: dark.brand,
        dark: dark.brandHover,
        contrastText: '#fff',
      },
      secondary: { main: dark.secondary, contrastText: '#fff' },
      error: { main: dark.error, dark: dark.errorContained },
      success: { main: dark.success },
      warning: { main: dark.warning },
      background: { default: dark.base, paper: dark.surface },
      text: { primary: dark.textPrimary, secondary: dark.textSecondary },
      divider: dark.border,
      action: {
        hover: dark.brandTint,
        selected: dark.brandSoft,
        disabled: 'rgba(255, 255, 255, 0.6)',
        disabledBackground: 'rgba(255, 255, 255, 0.16)',
      },
    };
  }
  return {
    mode: 'light',
    primary: {
      light: light.brand,
      main: light.brand,
      dark: light.brandHover,
      contrastText: '#fff',
    },
    secondary: { main: light.secondary, contrastText: '#fff' },
    error: { main: light.error },
    success: { main: market.light.up },
    background: { default: light.base, paper: light.surface },
    text: { primary: light.textPrimary, secondary: light.textSecondary },
    divider: light.border,
    action: { hover: light.neutralHover, selected: light.brandSoft },
  };
}

export function buildTheme(mode) {
  const isDark = mode === 'dark';
  const c = isDark ? dark : light;
  const m = isDark ? market.dark : market.light;
  const tableHeadBg = isDark ? dark.header : light.neutralHover;

  const theme = createTheme({
    palette: buildPalette(mode),
    typography,
    shape: { borderRadius: shared.borderRadius },
    spacing: shared.spacing,
    // Exposed so components can reach market colours without importing tokens.
    market: m,
    layout: shared,
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            backgroundColor: c.base,
            fontFamily: shared.fontFamily,
          },
          // Tabular figures everywhere prices are shown, so digits do not
          // jitter horizontally as they tick.
          '.numeric': {
            fontVariantNumeric: 'tabular-nums',
            fontFeatureSettings: '"tnum"',
          },
          '::-webkit-scrollbar': { width: 10, height: 10 },
          '::-webkit-scrollbar-thumb': {
            backgroundColor: isDark ? '#3A424F' : '#C9CDD4',
            borderRadius: 8,
          },
          '::-webkit-scrollbar-track': { backgroundColor: 'transparent' },
        },
      },
      MuiAppBar: {
        defaultProps: { elevation: 0, color: 'inherit' },
        styleOverrides: {
          root: {
            backgroundColor: c.surface,
            color: c.textPrimary,
            boxShadow: isDark ? 'none' : light.appBarShadow,
            borderBottom: isDark ? `1px solid ${dark.border}` : 'none',
          },
        },
      },
      MuiDrawer: {
        styleOverrides: {
          paper: {
            backgroundColor: c.surface,
            color: c.textPrimary,
            borderRadius: 0,
            borderRight: `1px solid ${isDark ? dark.border : light.borderSubtle}`,
          },
        },
      },
      MuiPaper: {
        defaultProps: { elevation: 0 },
        styleOverrides: {
          // The portal removes shadows at every elevation and leans on borders.
          root: {
            backgroundImage: 'none',
            boxShadow: 'none',
            borderRadius: shared.borderRadius,
          },
        },
      },
      MuiCard: {
        styleOverrides: {
          root: {
            overflow: 'hidden',
            boxShadow: 'none',
            borderRadius: shared.borderRadius,
            border: `1px solid ${c.border}`,
          },
        },
      },
      MuiButton: {
        disableElevation: true,
        styleOverrides: {
          root: {
            borderRadius: shared.borderRadius,
            textTransform: 'none',
            fontWeight: 600,
            boxShadow: 'none',
            padding: '8px 16px',
            '&:hover': { boxShadow: 'none' },
          },
          containedPrimary: {
            backgroundColor: c.brand,
            color: '#fff',
            '&:hover': { backgroundColor: c.brandHover, boxShadow: 'none' },
            '&:disabled': {
              backgroundColor: isDark ? 'rgba(255,255,255,0.16)' : light.brandDisabled,
              color: isDark ? 'rgba(255,255,255,0.6)' : '#FFFFFFB3',
            },
          },
          textPrimary: {
            color: isDark ? dark.link : light.brand,
            '&:hover': { backgroundColor: c.brandTint },
          },
          outlinedPrimary: {
            color: isDark ? dark.link : light.brand,
            borderColor: c.brand,
            '&:hover': { borderColor: c.brand, backgroundColor: c.brandTint },
          },
        },
      },
      MuiTableContainer: {
        styleOverrides: {
          root: {
            overflowX: 'auto',
            borderRadius: shared.borderRadius,
            border: `1px solid ${c.border}`,
          },
        },
      },
      MuiTable: {
        styleOverrides: {
          root: {
            borderCollapse: 'separate',
            borderSpacing: 0,
            borderRadius: shared.borderRadius,
            overflow: 'hidden',
          },
        },
      },
      MuiTableCell: {
        styleOverrides: {
          root: { borderColor: c.border },
          head: {
            lineHeight: 'normal',
            backgroundColor: tableHeadBg,
            fontWeight: 500,
            fontSize: '0.8125rem',
            padding: '12px 16px',
            color: c.textPrimary,
            whiteSpace: 'nowrap',
          },
          body: {
            padding: '10px 16px',
            fontSize: '0.8125rem',
            color: c.textPrimary,
          },
        },
      },
      MuiTableRow: {
        styleOverrides: {
          root: {
            '&:hover': { backgroundColor: isDark ? dark.neutralHover : light.neutralHover },
            '&:last-child .MuiTableCell-body': { borderBottom: 'none' },
          },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: { backgroundColor: c.chip, transition: 'none', fontWeight: 500 },
          label: { fontSize: '0.75rem', fontWeight: 500 },
        },
      },
      MuiTooltip: {
        styleOverrides: {
          tooltip: {
            backgroundColor: c.tooltip,
            color: '#fff',
            fontSize: '0.75rem',
          },
          arrow: { color: c.tooltip },
        },
      },
      MuiOutlinedInput: {
        styleOverrides: {
          root: {
            borderRadius: shared.borderRadius,
            '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: c.brand },
            '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
              borderColor: c.brand,
              borderWidth: 1,
            },
          },
          notchedOutline: { borderColor: c.border },
          input: { paddingTop: 10, paddingBottom: 10 },
        },
      },
      MuiListItemButton: {
        styleOverrides: {
          root: {
            borderRadius: shared.borderRadius,
            color: c.textPrimary,
            '&.Mui-selected': {
              backgroundColor: isDark ? dark.brandSoft : light.brandSoft,
              color: isDark ? dark.link : light.brand,
              '&:hover': { backgroundColor: isDark ? dark.brandSoft : light.brandSoft },
            },
            '&:hover': { backgroundColor: isDark ? dark.neutralHover : light.neutralHover },
          },
        },
      },
      MuiTab: {
        styleOverrides: {
          root: { textTransform: 'none', fontWeight: 600, fontSize: '0.875rem' },
        },
      },
      // An inline link in prose. `dark.link` already exists as a token and was
      // already wired to the text and outlined buttons; MuiLink was not, so a
      // link inside a sentence fell back to the indigo accent, which is a
      // brand colour chosen against a WHITE surface and is close to unreadable
      // on the dark one. Same rule as the buttons, so the two agree.
      MuiLink: {
        styleOverrides: {
          root: {
            color: isDark ? dark.link : light.brand,
            textDecorationColor: 'currentColor',
          },
        },
      },
      MuiAlert: { styleOverrides: { root: { borderRadius: shared.borderRadius } } },
      MuiDialog: { styleOverrides: { paper: { border: `1px solid ${c.border}` } } },
      MuiMenu: { styleOverrides: { paper: { border: `1px solid ${c.border}` } } },
    },
  });

  return theme;
}

export const lightTheme = buildTheme('light');
export const darkTheme = buildTheme('dark');
