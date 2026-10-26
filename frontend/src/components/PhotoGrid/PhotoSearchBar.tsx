import { X } from "lucide-react";
import { PHOTO_SEARCH_QUERY_MAX_LENGTH } from "../../types/photos";

interface PhotoSearchBarProps {
  query: string;
  isSearching: boolean;
  onQueryChange: (value: string) => void;
  onSubmit: (query: string) => void;
  onClear: () => void;
}

export function PhotoSearchBar({
  query,
  isSearching,
  onQueryChange,
  onSubmit,
  onClear,
}: PhotoSearchBarProps) {
  const canClear = query.length > 0;

  return (
    <form
      className="filters"
      role="search"
      aria-busy={isSearching || undefined}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit(query);
      }}
    >
      <label htmlFor="photo-search">Search</label>
      <input
        id="photo-search"
        name="photo-search"
        type="text"
        role="searchbox"
        placeholder="Describe a photo…"
        autoComplete="off"
        spellCheck={false}
        enterKeyHint="search"
        maxLength={PHOTO_SEARCH_QUERY_MAX_LENGTH}
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
      />
      {canClear && (
        <button type="button" className="icon-btn" aria-label="Clear search" onClick={onClear}>
          <X size={18} aria-hidden="true" />
        </button>
      )}
    </form>
  );
}
