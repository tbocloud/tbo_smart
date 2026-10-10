import { useState } from "react";

import type { HoldReason, Person } from "@/api/types";
import { Avatar, Button, OptionList, Sheet, Text, TextField } from "@/components";
import { en } from "@/i18n/en";

export const HOLD_REASONS: HoldReason[] = [
  "Waiting on customer",
  "Waiting on a teammate",
  "Blocked by another task",
  "Waiting for access",
  "Other",
];

/**
 * Each sheet keeps its form state inside; callers remount it (`key`) per opening
 * so a reopened sheet starts empty.
 */
type Base = { open: boolean; onClose: () => void; pending: boolean };

export function CompleteSheet({
  open,
  onClose,
  pending,
  onSubmit,
  subject,
}: Base & { subject: string; onSubmit: (hours: number, note: string) => void }) {
  const [hoursText, setHoursText] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | undefined>();

  const submit = () => {
    const value = Number(hoursText.replace(",", "."));
    if (!hoursText.trim() || Number.isNaN(value) || value < 0 || value > 24) {
      setError(en.task.hoursInvalid);
      return;
    }
    onSubmit(value, note.trim());
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={en.task.completeTitle}
      subtitle={subject}
      footer={<Button label={en.task.completeAction} variant="primary" size="lg" full loading={pending} onPress={submit} />}
    >
      <TextField
        label={en.task.completeHours}
        help={en.task.completeHoursHelp}
        error={error}
        value={hoursText}
        onChangeText={(v) => {
          setHoursText(v);
          setError(undefined);
        }}
        keyboardType="decimal-pad"
        placeholder="1.5"
        mono
      />
      <TextField
        label={en.task.completeNote}
        optional
        value={note}
        onChangeText={setNote}
        placeholder={en.task.completeNotePlaceholder}
        multiline
      />
    </Sheet>
  );
}

export function HoldSheet({
  open,
  onClose,
  pending,
  onSubmit,
  subject,
}: Base & { subject: string; onSubmit: (reason: HoldReason, note: string) => void }) {
  const [reason, setReason] = useState<HoldReason | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | undefined>();
  const needsNote = reason === "Other";

  const submit = () => {
    if (!reason) return;
    if (needsNote && !note.trim()) {
      setError(en.task.holdNoteRequired);
      return;
    }
    onSubmit(reason, note.trim());
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={en.task.holdTitle}
      subtitle={subject}
      footer={
        <Button
          label={en.task.holdAction}
          variant="primary"
          size="lg"
          full
          disabled={!reason}
          loading={pending}
          onPress={submit}
        />
      }
    >
      <Text variant="small" weight="medium" color="ink7">
        {en.task.holdWhy}
      </Text>
      <OptionList
        options={HOLD_REASONS.map((r) => ({ value: r, label: r }))}
        value={reason}
        onChange={(r) => {
          setReason(r);
          setError(undefined);
        }}
      />
      <TextField
        label={en.task.holdNote}
        optional={!needsNote}
        error={error}
        help={needsNote ? en.task.holdNoteRequired : undefined}
        value={note}
        onChangeText={(v) => {
          setNote(v);
          setError(undefined);
        }}
        placeholder={en.task.holdNotePlaceholder}
        multiline
      />
    </Sheet>
  );
}

export function PersonSheet({
  open,
  onClose,
  pending,
  onSubmit,
  people,
  mode,
}: Base & { people: Person[]; mode: "handover" | "help"; onSubmit: (to: Person, note: string) => void }) {
  const [picked, setPicked] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const person = people.find((p) => p.user === picked);
  const action = person
    ? mode === "handover"
      ? en.task.handOverAction(person.full_name.split(" ")[0])
      : en.task.helpAction(person.full_name.split(" ")[0])
    : en.task.pickPerson;

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={mode === "handover" ? en.task.handOverTitle : en.task.helpTitle}
      footer={
        <Button
          label={action}
          variant="primary"
          size="lg"
          full
          disabled={!person}
          loading={pending}
          onPress={() => person && onSubmit(person, note.trim())}
        />
      }
    >
      <OptionList
        options={people.map((p) => ({
          value: p.user,
          label: p.full_name,
          description: p.role,
          leading: <Avatar name={p.full_name} size={36} />,
        }))}
        value={picked}
        onChange={setPicked}
      />
      <TextField
        label={en.task.handNote}
        optional
        value={note}
        onChangeText={setNote}
        placeholder={en.task.handNotePlaceholder}
        multiline
      />
    </Sheet>
  );
}
