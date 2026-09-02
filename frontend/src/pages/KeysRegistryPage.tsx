import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Copy, Plus, Trash2 } from "lucide-react";
import { PrivateUnlockModal } from "../components/PrivateUnlockModal";
import { Button } from "../components/ui/Button";
import { ErrorMessage } from "../components/ui/ErrorMessage";
import { Input } from "../components/ui/Input";
import { Modal } from "../components/ui/Modal";
import { usePrivateSession } from "../hooks/usePrivateSession";
import { getApiErrorDetail } from "../services/api";
import { listKeys, saveKeys, type RegistryKey } from "../services/keysApi";
import { showErrorToast, showSuccessToast } from "../utils/toast";

function maskKeyValue(value: string): string {
  if (value.length <= 8) {
    return "••••";
  }
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

function keysSignature(keys: RegistryKey[]): string {
  return JSON.stringify(keys);
}

export default function KeysRegistryPage() {
  const navigate = useNavigate();
  const { isActive, isLoading, showUnlockModal, onUnlockSuccess } = usePrivateSession();
  const [keys, setKeys] = useState<RegistryKey[]>([]);
  const [savedSignature, setSavedSignature] = useState("");
  const [isFetching, setIsFetching] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | undefined>();
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newValue, setNewValue] = useState("");
  const [addError, setAddError] = useState<string | undefined>();

  const isPristine = useMemo(
    () => keysSignature(keys) === savedSignature,
    [keys, savedSignature],
  );

  const loadRegistry = useCallback(async () => {
    setIsFetching(true);
    setLoadError(undefined);

    try {
      const data = await listKeys();
      setKeys(data.keys);
      setSavedSignature(keysSignature(data.keys));
    } catch (error) {
      const detail = getApiErrorDetail(error);
      if (detail?.error_code === "PRIVATE_SESSION_EXPIRED") {
        return;
      }
      setLoadError(detail?.error_code ?? "INTERNAL_ERROR");
      showErrorToast(error);
    } finally {
      setIsFetching(false);
    }
  }, []);

  useEffect(() => {
    if (!isActive) {
      return;
    }
    void loadRegistry();
  }, [isActive, loadRegistry]);

  const handleCancelUnlock = useCallback(() => {
    navigate("/files");
  }, [navigate]);

  const handleUnlockSuccess = useCallback(() => {
    onUnlockSuccess();
  }, [onUnlockSuccess]);

  const handleCopy = useCallback(async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      showSuccessToast("Copied to clipboard");
    } catch {
      showErrorToast(new Error("Could not copy to clipboard"));
    }
  }, []);

  const handleDelete = useCallback((name: string) => {
    setKeys((current) => current.filter((key) => key.name !== name));
  }, []);

  const closeAddModal = useCallback(() => {
    setIsAddOpen(false);
    setNewName("");
    setNewValue("");
    setAddError(undefined);
  }, []);

  const handleAdd = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const name = newName.trim();
      const value = newValue.trim();

      if (!name) {
        setAddError("Key name cannot be empty");
        return;
      }
      if (!value) {
        setAddError("Key value cannot be empty");
        return;
      }
      if (keys.some((key) => key.name === name)) {
        setAddError("A key with this name already exists");
        return;
      }

      setKeys((current) => [...current, { name, value }]);
      closeAddModal();
    },
    [closeAddModal, keys, newName, newValue],
  );

  const handleSave = useCallback(async () => {
    setIsSaving(true);
    try {
      const data = await saveKeys(keys);
      setKeys(data.keys);
      setSavedSignature(keysSignature(data.keys));
      showSuccessToast("Keys saved");
    } catch (error) {
      const detail = getApiErrorDetail(error);
      if (detail?.error_code === "PRIVATE_SESSION_EXPIRED") {
        return;
      }
      showErrorToast(error);
    } finally {
      setIsSaving(false);
    }
  }, [keys]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-zinc-100">Keys Registry</h2>
        {isActive && (
          <div className="flex gap-2">
            <Button
              type="button"
              variant="secondary"
              className="w-auto"
              onClick={() => setIsAddOpen(true)}
            >
              <Plus size={16} className="mr-1.5" />
              Add key
            </Button>
            <Button
              type="button"
              className="w-auto"
              disabled={isPristine || isSaving}
              isLoading={isSaving}
              onClick={() => {
                void handleSave();
              }}
            >
              Save
            </Button>
          </div>
        )}
      </div>

      {isActive && (
        <div className="min-h-0 flex-1 overflow-auto">
          {isFetching ? (
            <div className="flex justify-center py-12">
              <span className="h-8 w-8 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
            </div>
          ) : loadError ? (
            <ErrorMessage errorCode={loadError} />
          ) : keys.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-zinc-700 px-6 py-16 text-center">
              <p className="text-sm text-zinc-400">No keys yet</p>
              <Button
                type="button"
                variant="secondary"
                className="w-auto"
                onClick={() => setIsAddOpen(true)}
              >
                <Plus size={16} className="mr-1.5" />
                Add key
              </Button>
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-zinc-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-zinc-900/80 text-xs uppercase tracking-wide text-zinc-500">
                  <tr>
                    <th className="px-4 py-3 font-medium">Key name</th>
                    <th className="px-4 py-3 font-medium">Value</th>
                    <th className="px-4 py-3 font-medium">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800">
                  {keys.map((key) => (
                    <tr key={key.name} className="bg-zinc-950/40">
                      <td className="px-4 py-3 font-medium text-zinc-100">{key.name}</td>
                      <td className="px-4 py-3 font-mono text-zinc-400">
                        {maskKeyValue(key.value)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          <button
                            type="button"
                            title="Copy value"
                            aria-label={`Copy ${key.name}`}
                            className="rounded-lg p-2 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
                            onClick={() => {
                              void handleCopy(key.value);
                            }}
                          >
                            <Copy size={16} />
                          </button>
                          <button
                            type="button"
                            title="Delete key"
                            aria-label={`Delete ${key.name}`}
                            className="rounded-lg p-2 text-zinc-400 transition-colors hover:bg-red-500/10 hover:text-red-400"
                            onClick={() => handleDelete(key.name)}
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <PrivateUnlockModal
        isOpen={showUnlockModal}
        onSuccess={handleUnlockSuccess}
        onCancel={handleCancelUnlock}
      />

      <Modal isOpen={isAddOpen} onClose={closeAddModal} title="Add key">
        <form className="flex flex-col gap-4" onSubmit={handleAdd}>
          <Input
            label="Key name"
            value={newName}
            onChange={(event) => {
              setNewName(event.target.value);
              setAddError(undefined);
            }}
            autoComplete="off"
          />
          <Input
            label="Value"
            type="password"
            value={newValue}
            onChange={(event) => {
              setNewValue(event.target.value);
              setAddError(undefined);
            }}
            autoComplete="off"
          />
          {addError && <p className="text-xs text-red-400">{addError}</p>}
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={closeAddModal}>
              Cancel
            </Button>
            <Button type="submit">Add</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
