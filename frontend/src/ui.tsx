import { createContext, useCallback, useContext, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { AlertDialog } from "@base-ui/react/alert-dialog";
import { Button as BaseButton } from "@base-ui/react/button";
import { Check, Warning, X } from "@phosphor-icons/react";
import { cva } from "class-variance-authority";
import clsx from "clsx";

const buttonStyles = cva("archive-button", {
  variants: {
    tone: {
      primary: "archive-button-primary",
      secondary: "archive-button-secondary",
      quiet: "archive-button-quiet",
      danger: "archive-button-danger",
    },
    size: {
      standard: "archive-button-standard",
      compact: "archive-button-compact",
    },
  },
  defaultVariants: { tone: "secondary", size: "standard" },
});

export function Button({ className, tone, size, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: "primary" | "secondary" | "quiet" | "danger";
  size?: "standard" | "compact";
  children: ReactNode;
}) {
  return (
    <BaseButton className={clsx(buttonStyles({ tone, size }), className)} {...props}>
      {children}
    </BaseButton>
  );
}

type ConfirmOptions = {
  title: string;
  description: string;
  confirmLabel: string;
  tone?: "primary" | "danger";
};

type ConfirmRequest = ConfirmOptions & {
  resolve: (accepted: boolean) => void;
};

const ConfirmDialogContext = createContext<((options: ConfirmOptions) => Promise<boolean>) | null>(null);

export function ConfirmDialogProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);

  const confirm = useCallback((options: ConfirmOptions) => new Promise<boolean>((resolve) => {
    setRequest({ ...options, resolve });
  }), []);

  const finish = useCallback((accepted: boolean) => {
    setRequest((current) => {
      current?.resolve(accepted);
      return null;
    });
  }, []);

  return (
    <ConfirmDialogContext.Provider value={confirm}>
      {children}
      <AlertDialog.Root open={request !== null} onOpenChange={(open) => { if (!open) finish(false); }}>
        <AlertDialog.Portal>
          <AlertDialog.Backdrop className="archive-dialog-backdrop" />
          <AlertDialog.Viewport className="archive-dialog-viewport">
            <AlertDialog.Popup className="archive-dialog-popup">
              <AlertDialog.Title className="archive-dialog-title">{request?.title}</AlertDialog.Title>
              <AlertDialog.Description className="archive-dialog-description">{request?.description}</AlertDialog.Description>
              <div className="archive-dialog-actions">
                <AlertDialog.Close className="archive-button archive-button-quiet archive-button-standard">Cancel</AlertDialog.Close>
                <Button tone={request?.tone ?? "primary"} onClick={() => finish(true)}>{request?.confirmLabel ?? "Confirm"}</Button>
              </div>
            </AlertDialog.Popup>
          </AlertDialog.Viewport>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </ConfirmDialogContext.Provider>
  );
}

export function useConfirmDialog() {
  const confirm = useContext(ConfirmDialogContext);
  if (!confirm) throw new Error("useConfirmDialog must be used inside ConfirmDialogProvider");
  return confirm;
}

export function StatusMark({ status, label }: { status: "ready" | "missing" | "incompatible"; label: string }) {
  const Icon = status === "ready" ? Check : status === "missing" ? Warning : X;
  return (
    <span className={clsx("status-mark", `status-mark-${status}`)}>
      <span aria-hidden="true" className="status-mark-glyph"><Icon size={12} weight="bold" /></span>
      <span>{label}</span>
    </span>
  );
}
