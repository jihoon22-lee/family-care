const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function captureActiveElement(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const active = document.activeElement;
  return active instanceof HTMLElement ? active : null;
}

export function focusElement(element: HTMLElement | null): void {
  if (!element || !element.isConnected) return;
  element.focus({ preventScroll: true });
}

export function focusHeading(heading: HTMLElement | null): void {
  focusElement(heading);
}

export function restoreFocus(element: HTMLElement | null): void {
  focusElement(element);
}

export function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  );
}
