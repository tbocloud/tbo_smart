/**
 * TBO theme tokens for mobile.
 *
 * Values are the web app's `desk/src/theme.css` (helpdesk repo) converted from
 * oklch to sRGB hex, so the app and the web read as one product. Names follow
 * the web tokens: surface (backgrounds), ink (text), outline (borders), brand
 * (the one accent) and the status tones, which only ever carry meaning.
 */

export type ColorTokens = {
  bg: string; // app canvas behind cards
  surface: string; // cards, sheets, tab bar
  surfaceSunken: string; // inset areas inside cards, inputs
  surfaceRaised: string; // pressed rows, skeleton base
  surfaceStrong: string; // skeleton shimmer, dividers that need weight

  ink9: string; // headings, primary text
  ink7: string; // body text
  ink6: string; // secondary text
  ink5: string; // meta text (passes 4.5:1 on surface)
  ink4: string; // placeholders, disabled
  inkInverse: string;

  outline1: string; // hairlines inside cards
  outline2: string; // card borders
  outline3: string; // input borders
  outline4: string; // focused neutral borders

  brand: string;
  brandPressed: string;
  brandSoft: string;
  brandInk: string;
  onBrand: string;

  success: string;
  successSoft: string;
  warning: string;
  warningSoft: string;
  danger: string;
  dangerSoft: string;
  info: string;
  infoSoft: string;

  note: string;
  noteBorder: string;

  champion: string;
  championSoft: string;
  championBorder: string;

  scrim: string;
};

export const light: ColorTokens = {
  bg: "#F6F7F9",
  surface: "#FFFFFF",
  surfaceSunken: "#F1F2F5",
  surfaceRaised: "#ECEEF2",
  surfaceStrong: "#E0E2E8",

  ink9: "#15171C",
  ink7: "#393D46",
  ink6: "#555B66",
  ink5: "#646A75",
  ink4: "#8D929C",
  inkInverse: "#FFFFFF",

  outline1: "#EBEDF1",
  outline2: "#E5E7EB",
  outline3: "#CFD3DA",
  outline4: "#A0A5AE",

  brand: "#5C44E0",
  brandPressed: "#4B35CC",
  brandSoft: "#EFEDFD",
  brandInk: "#4430C2",
  onBrand: "#FFFFFF",

  success: "#1C7547",
  successSoft: "#E6F4EC",
  warning: "#955700",
  warningSoft: "#FDF2DE",
  danger: "#BF3328",
  dangerSoft: "#FDEBE9",
  info: "#1E5FBF",
  infoSoft: "#E7EFFB",

  note: "#FFFAE4",
  noteBorder: "#EBDB9E",

  champion: "#8A5600",
  championSoft: "#FDF7E8",
  championBorder: "#EDD59C",

  scrim: "rgba(13, 15, 19, 0.45)",
};

export const dark: ColorTokens = {
  bg: "#0E0F12",
  surface: "#16171B",
  surfaceSunken: "#1C1E23",
  surfaceRaised: "#24262C",
  surfaceStrong: "#2B2E35",

  ink9: "#ECEEF2",
  ink7: "#D6D9DF",
  ink6: "#BABEC6",
  ink5: "#A3A8B3",
  ink4: "#878D98",
  inkInverse: "#16171B",

  outline1: "#21242A",
  outline2: "#262930",
  outline3: "#3A3E47",
  outline4: "#535760",

  brand: "#9284FF",
  brandPressed: "#A297FF",
  brandSoft: "#231F40",
  brandInk: "#BCB3FF",
  onBrand: "#141030",

  success: "#52C48D",
  successSoft: "#14281E",
  warning: "#F0B44C",
  warningSoft: "#2D2311",
  danger: "#FF8C82",
  dangerSoft: "#34191A",
  info: "#72AAFF",
  infoSoft: "#15233A",

  note: "#262210",
  noteBorder: "#453D1D",

  champion: "#F0BF5E",
  championSoft: "#2A2213",
  championBorder: "#4F3F1C",

  scrim: "rgba(0, 0, 0, 0.6)",
};

/** 4px grid, same steps as the web guidelines (§6). */
export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const;

export const radius = {
  sm: 6,
  md: 8,
  lg: 12,
  xl: 16,
  pill: 999,
} as const;

/** Minimum touch target (Material 48dp; also covers iOS 44pt). */
export const TAP = 48;

export const fonts = {
  regular: "Geist_400Regular",
  medium: "Geist_500Medium",
  semibold: "Geist_600SemiBold",
  mono: "GeistMono_400Regular",
  monoMedium: "GeistMono_500Medium",
} as const;

/**
 * Mobile type scale. One step larger than the web's dense desk scale so body
 * text stays readable at arm's length; line heights are on the 4px grid.
 */
export const type = {
  caption: { fontSize: 12, lineHeight: 16 },
  small: { fontSize: 13, lineHeight: 18 },
  body: { fontSize: 15, lineHeight: 20 },
  title: { fontSize: 17, lineHeight: 24 },
  h2: { fontSize: 20, lineHeight: 28 },
  h1: { fontSize: 24, lineHeight: 32 },
  display: { fontSize: 32, lineHeight: 40 },
} as const;

export type TypeVariant = keyof typeof type;

/** Meaning → tone. The only colours allowed behind icons (guidelines §8 exception). */
export type Tone = "neutral" | "brand" | "success" | "warning" | "danger" | "info" | "champion";

export function toneColors(c: ColorTokens, tone: Tone): { fg: string; bg: string } {
  switch (tone) {
    case "brand":
      return { fg: c.brandInk, bg: c.brandSoft };
    case "success":
      return { fg: c.success, bg: c.successSoft };
    case "warning":
      return { fg: c.warning, bg: c.warningSoft };
    case "danger":
      return { fg: c.danger, bg: c.dangerSoft };
    case "info":
      return { fg: c.info, bg: c.infoSoft };
    case "champion":
      return { fg: c.champion, bg: c.championSoft };
    default:
      return { fg: c.ink6, bg: c.surfaceSunken };
  }
}
