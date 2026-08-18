/**
 * Theme augmentation for the Vision UI template.
 *
 * The template's theme carries keys MUI's own `Theme` does not know about —
 * `borders`, `functions`, and a `size` scale on typography — and the template's
 * own components read them straight off `useTheme()`. Declared here so app
 * components can do the same instead of hardcoding values the theme already
 * owns.
 *
 * Component *props* are handled separately, in `src/components/vision.ts`:
 * those modules resolve to real .js files, so an ambient declaration for them
 * would be treated as an augmentation and lose to TypeScript's own inference.
 */

export {};

declare module "@mui/material/styles" {
  interface Theme {
    borders: {
      borderWidth: string[];
      borderRadius: Record<string, string>;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      [key: string]: any;
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    functions: Record<string, any>;
  }

  interface ThemeOptions {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    borders?: Record<string, any>;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    functions?: Record<string, any>;
  }

  /**
   * The template's surface ramp, page outwards — `page`, `card`, `raised`,
   * `deep`, `muted`, plus `hover` for a row under the cursor. Built in
   * `assets/theme/base/colors.js`; declared here so TS call sites can read it
   * off `useTheme().palette` the way the template's own .js components do.
   */
  interface Palette {
    surfaces: Record<string, string>;
  }

  interface PaletteOptions {
    surfaces?: Record<string, string>;
  }

  interface TypographyVariants {
    size: Record<string, string>;
  }

  interface TypographyVariantsOptions {
    size?: Record<string, string>;
  }
}
