"use client";

import { useEffect, useState } from "react";

const TOKENS = {
  series: "--series-1",
  annotation: "--annotation",
  shade: "--shade",
  barOff: "--bar-off",
  grid: "--border",
  text: "--text-secondary",
  surface: "--surface-1",
} as const;

type Colors = Record<keyof typeof TOKENS, string>;

const LIGHT: Colors = {
  series: "#2a78d6", annotation: "#8a8984", shade: "#8a8984", barOff: "#c3c2b7",
  grid: "#e1e0da", text: "#52514e", surface: "#fcfcfb",
};

function read(): Colors {
  const cs = getComputedStyle(document.documentElement);
  return Object.fromEntries(
    Object.entries(TOKENS).map(([k, v]) => [k, cs.getPropertyValue(v).trim() || LIGHT[k as keyof Colors]]),
  ) as Colors;
}

/**
 * SVG presentation attributes (what Recharts sets) don't resolve CSS
 * variables, so resolve the theme tokens here and follow light/dark changes.
 */
export function useThemeColors(): Colors {
  const [colors, setColors] = useState<Colors>(LIGHT);
  useEffect(() => {
    setColors(read());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setColors(read());
    mq.addEventListener("change", update);
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      mq.removeEventListener("change", update);
      mo.disconnect();
    };
  }, []);
  return colors;
}
