// ESLint flat config.
//
// `next lint` was the previous setup and it does not work unattended: with
// no config present it prompts "How would you like to configure ESLint?"
// and waits for a keypress, which in CI is a hung step that eventually
// fails. It is also deprecated and removed in Next.js 16.
//
// So the config is explicit and the script calls the ESLint CLI directly.

import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "public/**",
    ],
  },
  // eslint-config-next is still an eslintrc-style shareable config, so it
  // needs FlatCompat to load under flat config.
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // Unused variables are a real signal in this codebase - a dropped
      // prop or a stale import after a refactor. Error, but allow the
      // _-prefix convention for deliberate placeholders.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];

export default config;
