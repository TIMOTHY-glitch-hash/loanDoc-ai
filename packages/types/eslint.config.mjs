import js from '@eslint/js';
import tseslint from 'typescript-eslint';

/**
 * Flat config (ESLint 9). Type-aware linting is enabled so rules such as
 * `no-unsafe-assignment` can catch contract drift between Zod schemas and the
 * inferred TypeScript types.
 */
export default tseslint.config(
  { ignores: ['dist/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
);
