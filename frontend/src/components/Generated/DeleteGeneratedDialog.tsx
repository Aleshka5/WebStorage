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
      <p>{label} will be permanently deleted.</p>
      {errorCode && <ErrorMessage errorCode={errorCode} />}
      <div className="actions">
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
