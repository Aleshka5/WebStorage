import { FormEvent, useEffect, useState } from "react";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { Modal } from "../ui/Modal";
import { validateFileName } from "../../utils/validation";

interface CreateFolderDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (name: string) => Promise<void>;
}

export function CreateFolderDialog({ isOpen, onClose, onCreate }: CreateFolderDialogProps) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | undefined>();
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isOpen) {
      setName("");
      setError(undefined);
      setIsSubmitting(false);
    }
  }, [isOpen]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const validationError = validateFileName(name);

    if (validationError) {
      setError(validationError);
      return;
    }

    setIsSubmitting(true);
    setError(undefined);

    try {
      await onCreate(name.trim());
      onClose();
    } catch {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="New folder">
      <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
        <Input
          label="Folder name"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            if (error) {
              setError(undefined);
            }
          }}
          error={error}
          autoFocus
          disabled={isSubmitting}
          placeholder="Enter a name"
        />
        <div className="flex gap-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button type="submit" isLoading={isSubmitting}>
            Create
          </Button>
        </div>
      </form>
    </Modal>
  );
}
