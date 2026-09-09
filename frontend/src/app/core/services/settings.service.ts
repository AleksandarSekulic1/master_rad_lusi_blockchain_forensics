import { Injectable, effect, signal } from '@angular/core';

export type ThemeMode = 'dark' | 'light';
export type AppLang = 'sr' | 'en';

const THEME_KEY = 'lusi_theme';
const LANG_KEY = 'lusi_lang';

@Injectable({
  providedIn: 'root',
})
export class SettingsService {
  readonly theme = signal<ThemeMode>(this.readTheme());
  readonly lang = signal<AppLang>(this.readLang());

  constructor() {
    // Apply immediately so there is no theme flash on first paint.
    this.applyTheme(this.theme());
    this.applyLang(this.lang());

    effect(() => {
      const theme = this.theme();
      this.applyTheme(theme);
      this.write(THEME_KEY, theme);
    });

    effect(() => {
      const lang = this.lang();
      this.applyLang(lang);
      this.write(LANG_KEY, lang);
    });
  }

  toggleTheme(): void {
    this.theme.update((mode) => (mode === 'dark' ? 'light' : 'dark'));
  }

  toggleLang(): void {
    this.lang.update((lang) => (lang === 'sr' ? 'en' : 'sr'));
  }

  private applyTheme(theme: ThemeMode): void {
    document.documentElement.setAttribute('data-theme', theme);
  }

  private applyLang(lang: AppLang): void {
    document.documentElement.setAttribute('lang', lang);
  }

  private readTheme(): ThemeMode {
    return this.read(THEME_KEY) === 'light' ? 'light' : 'dark';
  }

  private readLang(): AppLang {
    return this.read(LANG_KEY) === 'en' ? 'en' : 'sr';
  }

  private read(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  private write(key: string, value: string): void {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* storage unavailable – keep in-memory only */
    }
  }
}
