import { useState } from "react";

import { FieldButton } from "./FieldButton";
import { OptionList } from "./OptionList";
import { Sheet } from "./Sheet";

type Option<T extends string> = { value: T; label: string; description?: string };

export function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  placeholder,
  error,
  help,
}: {
  label: string;
  value: T | null;
  options: Option<T>[];
  onChange: (v: T) => void;
  placeholder: string;
  error?: string;
  help?: string;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find((o) => o.value === value);
  return (
    <>
      <FieldButton
        label={label}
        value={current?.label ?? null}
        placeholder={placeholder}
        onPress={() => setOpen(true)}
        error={error}
        help={help}
      />
      <Sheet open={open} onClose={() => setOpen(false)} title={label}>
        <OptionList
          options={options}
          value={value}
          onChange={(v) => {
            onChange(v);
            setOpen(false);
          }}
        />
      </Sheet>
    </>
  );
}
