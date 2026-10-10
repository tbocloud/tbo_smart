import type { Me, Person } from "@/api/types";

export const people = {
  najath: { user: "najath@teambackoffice.com", full_name: "Najath Thekkeyil", role: "Frappe Developer" },
  arun: { user: "arun.babu@teambackoffice.com", full_name: "Arun Babu", role: "Head of ERP" },
  gayathri: { user: "gayathri@teambackoffice.com", full_name: "Gayathri Sumithran", role: "Support Lead" },
  mufliha: { user: "mufliha@teambackoffice.com", full_name: "Mufliha", role: "Content Writer" },
  ajmal: { user: "ajmal.m@teambackoffice.com", full_name: "Ajmal M", role: "Graphic Designer" },
  niranjana: { user: "niranjana.c@teambackoffice.com", full_name: "Niranjana C", role: "ERP Consultant" },
  reshma: { user: "reshma.r@teambackoffice.com", full_name: "Reshma R", role: "ERP Consultant" },
  sanika: { user: "sanika@teambackoffice.com", full_name: "Sanika", role: "Developer" },
} satisfies Record<string, Person>;

export const me: Me = {
  ...people.najath,
  roles: ["Agent", "Project User"],
  is_manager: false,
  department: "ERP",
  teams: ["ERP Support"],
};

/** Teammates offered by "Hand over" and "Ask for help". */
export const teammates: Person[] = [
  people.arun,
  people.gayathri,
  people.niranjana,
  people.reshma,
  people.sanika,
  people.ajmal,
];
