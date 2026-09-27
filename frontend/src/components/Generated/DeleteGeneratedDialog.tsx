import { Button } from "../ui/Button";
import { ErrorMessage } from "../ui/ErrorMessage";
import { Modal } from "../ui/Modal";
import { formatDateTime } from "../../utils/format";

interface DeleteGeneratedDialogProps {
  isOpen: boolean;
  createdAt: string;
  isDeleting: boolean;
  errorCode?: string | null;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeleteGeneratedDialog({
  isOpen,
  createdAt,
  isDeleting,
  errorCode,
  onClose,
  onConfirm,
}: DeleteGeneratedDialogProps) {
  const label = formatDateTime(createdAt);

  return (
    <Modal
      isOpen={isOpen}
      onClose={() => {
        if (!isDeleting) {
          onClose();
        }
      }}
      title="Delete generated image?"
    >
      <p className="mb-4 text-sm text-zinc-300">
        {label} will be permanently deleted.
      </p>
      {errorCode && <ErrorMessage errorCode={errorCode} className="mb-4" />}
      <div className="flex gap-2">
        <Button type="button" variant="secondary" onClick={onClose} disabled={isDeleting}>
          Cancel
        </Button>
        <Button
          type="button"
          variant="danger"
          isLoading={isDeleting}
          onClick={onConfirm}
        >
          Delete
        </Button>
      </div>
    </Modal>
  );
}
