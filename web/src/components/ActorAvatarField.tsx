import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { ActorAvatar } from "./ActorAvatar";

type ActorAvatarFieldProps = {
  label?: string | null;
  avatarUrl?: string | null;
  previewUrl?: string | null;
  runtime?: string | null;
  title?: string | null;
  isDark: boolean;
  sizeClassName?: string;
  disabled?: boolean;
  resetDisabled?: boolean;
  uploadBusy?: boolean;
  resetBusy?: boolean;
  onSelectFile: (file: File | null) => void;
  onReset: () => void;
};

export function ActorAvatarField({
  label,
  avatarUrl,
  previewUrl,
  runtime,
  title,
  isDark,
  sizeClassName = "h-12 w-12",
  disabled = false,
  resetDisabled = false,
  uploadBusy = false,
  resetBusy = false,
  onSelectFile,
  onReset,
}: ActorAvatarFieldProps) {
  const { t } = useTranslation("actors");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const effectiveLabel = label === undefined ? t("avatarTitle") : label;
  const showReset = !resetDisabled || resetBusy;

  return (
    <div>
      {effectiveLabel ? (
        <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{effectiveLabel}</label>
      ) : null}
      <div className="flex flex-col items-center">
        <button
          type="button"
          className="group relative inline-flex rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/70 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg)] disabled:cursor-not-allowed"
          onClick={() => inputRef.current?.click()}
          disabled={disabled}
          aria-label={t("uploadAvatar")}
          title={t("uploadAvatar")}
        >
          <ActorAvatar
            avatarUrl={avatarUrl}
            previewUrl={previewUrl}
            runtime={runtime}
            title={title}
            isDark={isDark}
            sizeClassName={sizeClassName}
            textClassName="text-sm"
          />
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-full bg-black/0 text-white opacity-0 transition-all duration-150 group-hover:bg-black/45 group-hover:opacity-100 group-focus-visible:bg-black/45 group-focus-visible:opacity-100">
            <svg
              viewBox="0 0 24 24"
              className="h-5 w-5 drop-shadow-[0_1px_2px_rgba(0,0,0,0.45)]"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M4.5 8.5h3l1.4-2h6.2l1.4 2h3a1.5 1.5 0 0 1 1.5 1.5v7A1.5 1.5 0 0 1 19.5 18.5h-15A1.5 1.5 0 0 1 3 17v-7A1.5 1.5 0 0 1 4.5 8.5Z" />
              <circle cx="12" cy="13" r="3.2" />
            </svg>
          </span>
        </button>

        <input
          ref={inputRef}
          type="file"
          accept=".svg,.png,.jpg,.jpeg,.webp,.gif,.avif,.ico,image/*"
          className="hidden"
          onChange={(e) => {
            onSelectFile(e.target.files?.[0] || null);
            if (inputRef.current) inputRef.current.value = "";
          }}
        />

        <div className="mt-2 min-h-[1.25rem] text-center">
          {uploadBusy ? (
            <span className="text-[11px] font-medium text-[var(--color-text-muted)]">{t("avatarUploading")}</span>
          ) : resetBusy ? (
            <span className="text-[11px] font-medium text-[var(--color-text-muted)]">{t("avatarClearing")}</span>
          ) : showReset ? (
            <button
              type="button"
              className="rounded-full px-2 py-0.5 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--glass-tab-bg-hover)] hover:text-[var(--color-text-secondary)] disabled:opacity-50"
              onClick={onReset}
              disabled={disabled || resetDisabled}
            >
              {t("useDefaultAvatar")}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
