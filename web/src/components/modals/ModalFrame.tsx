import type { ReactNode, Ref } from "react";

interface ModalFrameProps {
  isOpen?: boolean;
  isDark: boolean;
  onClose: () => void;
  titleId: string;
  title: ReactNode;
  closeAriaLabel: string;
  panelClassName: string;
  headerActions?: ReactNode;
  modalRef?: Ref<HTMLDivElement>;
  children: ReactNode;
}

export function ModalFrame({
  isOpen = true,
  isDark: _isDark,
  onClose,
  titleId,
  title,
  closeAriaLabel,
  panelClassName,
  headerActions,
  modalRef,
  children,
}: ModalFrameProps) {
  return (
    <div
      className={`fixed inset-0 z-50 flex items-stretch justify-center p-0 transition-[opacity,visibility] duration-200 sm:items-center sm:p-4 ${
        isOpen ? "visible opacity-100 animate-fade-in" : "pointer-events-none invisible opacity-0"
      }`}
      style={isOpen ? { backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" } : undefined}
      aria-hidden={isOpen ? undefined : true}
    >
      <div
        className={`absolute inset-0 glass-overlay transition-opacity duration-200 ${isOpen ? "opacity-100" : "opacity-0"}`}
        onPointerDown={isOpen ? onClose : undefined}
        aria-hidden="true"
      />

      <div
        className={`relative flex flex-col rounded-none border shadow-2xl transition-[opacity,transform] duration-200 sm:rounded-xl glass-modal ${panelClassName} ${
          isOpen ? "opacity-100 animate-scale-in" : "pointer-events-none translate-y-2 scale-[0.985] opacity-0"
        }`}
        ref={modalRef}
        role="dialog"
        aria-modal={isOpen ? "true" : undefined}
        aria-labelledby={titleId}
      >
        <div
          className="flex flex-shrink-0 items-center justify-between px-5 py-4 border-b safe-area-inset-top border-[var(--glass-border-subtle)]"
        >
          <h2 id={titleId} className="min-w-0 flex-1 pr-3 text-lg font-semibold text-[var(--color-text-primary)]">
            {title}
          </h2>
          <div className="flex flex-shrink-0 items-center gap-1.5">
            {headerActions}
            <button
              onClick={onClose}
              className="text-xl leading-none min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg transition-colors glass-btn text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
              aria-label={closeAriaLabel}
            >
              ×
            </button>
          </div>
        </div>

        {children}
      </div>
    </div>
  );
}
