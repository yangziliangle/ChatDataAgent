/** 明暗主题：手动切换，localStorage 记忆。 */

const KEY = 'chatdataagent.theme';

export function getInitialTheme() {
  try {
    return localStorage.getItem(KEY) || 'light';
  } catch {
    return 'light';
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* 忽略存储失败 */
  }
}
