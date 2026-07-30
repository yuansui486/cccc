export function getComposerActionVisibility(isSmallScreen: boolean): {
  showMessageModeSelector: boolean;
} {
  return {
    showMessageModeSelector: !isSmallScreen,
  };
}
