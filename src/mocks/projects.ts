import type { Project, ProjectFile } from "@/api/types";
import { at } from "@/lib/clock";

export const projects: Project[] = [
  {
    name: "PROJ-0003",
    project_name: "TBO ERP",
    customer: "Team Back Office International LLP",
    department: "ERP",
    my_role: "Developer",
    open_tasks: 2,
    files: 4,
    for_you: 2,
  },
  {
    name: "PROJ-0011",
    project_name: "Avera IoT HR rollout",
    customer: "Avera IoT",
    department: "ERP",
    my_role: "Developer",
    open_tasks: 3,
    files: 2,
    for_you: 1,
  },
  {
    name: "PROJ-0008",
    project_name: "Galom ERP rollout",
    customer: "Galom International Private Limited",
    department: "ERP",
    my_role: "Developer",
    open_tasks: 2,
    files: 1,
    for_you: 0,
  },
  {
    name: "PROJ-0006",
    project_name: "TBO Copilot",
    customer: "Team Back Office International LLP",
    department: "R&D",
    my_role: "Developer",
    open_tasks: 1,
    files: 3,
    for_you: 1,
  },
];

export const projectFiles: Record<string, ProjectFile[]> = {
  "PROJ-0003": [
    {
      name: "FILE-0101",
      file_name: "Customer migration master prompt.md",
      folder: "Prompts",
      kind: "md",
      size: "6 KB",
      modified: at(-3, "11:20"),
      uploaded_by: "Arun Babu",
      for_you: true,
      status: "Final",
    },
    {
      name: "FILE-0102",
      file_name: "Data mapping notes.md",
      folder: "Prompts/Phase 2",
      kind: "md",
      size: "3 KB",
      modified: at(-1, "15:00"),
      uploaded_by: "Niranjana C",
      for_you: true,
      status: "Needs review",
    },
    {
      name: "FILE-0103",
      file_name: "Customer export Sep 2026.pdf",
      folder: "Source data",
      kind: "pdf",
      size: "1.2 MB",
      modified: at(-6, "10:00"),
      uploaded_by: "Haris K",
      for_you: false,
    },
    {
      name: "FILE-0104",
      file_name: "Go-live checklist.pdf",
      folder: "",
      kind: "pdf",
      size: "340 KB",
      modified: at(-8, "09:30"),
      uploaded_by: "Arun Babu",
      for_you: false,
      status: "Final",
    },
  ],
  "PROJ-0011": [
    {
      name: "FILE-0201",
      file_name: "Avera leave policy.pdf",
      folder: "Policies",
      kind: "pdf",
      size: "520 KB",
      modified: at(-4, "14:10"),
      uploaded_by: "Fathima Rasheed",
      for_you: true,
    },
    {
      name: "FILE-0202",
      file_name: "HR setup prompt.md",
      folder: "",
      kind: "md",
      size: "4 KB",
      modified: at(-2, "12:05"),
      uploaded_by: "Arun Babu",
      for_you: false,
      status: "Draft",
    },
  ],
  "PROJ-0008": [
    {
      name: "FILE-0301",
      file_name: "Galom invoice format v3.pdf",
      folder: "",
      kind: "pdf",
      size: "180 KB",
      modified: at(-1, "18:00"),
      uploaded_by: "Niranjana C",
      for_you: false,
    },
  ],
  "PROJ-0006": [
    {
      name: "FILE-0401",
      file_name: "Teams bot master prompt.md",
      folder: "Prompts",
      kind: "md",
      size: "8 KB",
      modified: at(-1, "10:00"),
      uploaded_by: "Arun Babu",
      for_you: true,
      status: "Final",
    },
    {
      name: "FILE-0402",
      file_name: "SSO sequence.pdf",
      folder: "Design",
      kind: "pdf",
      size: "96 KB",
      modified: at(-5, "16:45"),
      uploaded_by: "Sanika",
      for_you: false,
    },
    {
      name: "FILE-0403",
      file_name: "Bot replies tone guide.md",
      folder: "Prompts",
      kind: "md",
      size: "2 KB",
      modified: at(-5, "16:00"),
      uploaded_by: "Mufliha",
      for_you: false,
      status: "Final",
    },
  ],
};

/** Markdown bodies for `.md` files (what `get_project_file_text` returns). */
export const fileText: Record<string, string> = {
  "FILE-0101": `# Customer migration: master prompt

You are migrating **active customers** from the legacy ERP export into TBO ERP.

## Inputs
- \`customer_export_sep_2026.csv\` (214 rows)
- The customer group tree: Retail, Corporate, Government

## Rules
1. Skip rows where \`status = Inactive\` (31 rows).
2. Keep the GSTIN exactly as exported. Never guess one.
3. Create the billing address first, then link it to the customer.
4. Kakkanad branch customers go under **Corporate**.

## Output
Write an import log with one line per customer: name, result, and the reason for any skip.

\`\`\`
CUST-0001  created   Galom International Private Limited
CUST-0002  skipped   inactive in export
\`\`\`

Share the log with Arun before closing the task.`,
  "FILE-0102": `# Data mapping notes

- Legacy \`cust_type = R\` maps to **Retail**.
- Legacy \`cust_type = C\` maps to **Corporate**.
- Payment terms "30 days" become the *Net 30* template.

Open question: do government customers need a separate price list?`,
  "FILE-0202": `# HR setup for Avera IoT

1. Departments: Engineering, Sales, Accounts, Operations.
2. Holiday list 2027 for the Kochi branch (see ticket #1296).
3. Leave policy from **Avera leave policy.pdf**.`,
  "FILE-0401": `# Teams bot: master prompt

Build the sign-in for the TBO Copilot Teams bot.

## Goal
Staff open the bot in Teams and are signed in with their Microsoft account. **No second login.**

## Steps
1. Register the bot's Azure AD app with the redirect URI from the hub.
2. Exchange the Teams SSO token for a hub session.
3. If the email isn't a hub agent, reply politely and stop.

## Done when
- A new agent can ask "my tasks today" and get an answer within 3 seconds.`,
  "FILE-0403": `# Bot replies: tone

- Short, specific, friendly. One idea per message.
- Name the task or ticket. Never say "an error occurred".`,
};
