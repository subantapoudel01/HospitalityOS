// Shapes mirroring app/platform/schemas.py. Keep in sync when the API changes.

export const POLICY_CATEGORIES = [
  "checkin_checkout",
  "cancellation",
  "pets",
  "payment",
  "other",
] as const;

export type PolicyCategory = (typeof POLICY_CATEGORIES)[number];

export const POLICY_LABELS: Record<PolicyCategory, string> = {
  checkin_checkout: "Check-in / Check-out",
  cancellation: "Cancellation",
  pets: "Pets",
  payment: "Payment",
  other: "Other",
};

export interface RoomTypeIn {
  name: string;
  description?: string | null;
  base_rate: string;
  max_occupancy: number;
  amenities: string[];
}

export interface PolicyIn {
  category: PolicyCategory;
  content_text: string;
}

export interface HotelIn {
  name: string;
  description?: string | null;
  city?: string | null;
  address?: string | null;
  phone?: string | null;
  whatsapp_number?: string | null;
  currency?: string;
  timezone?: string;
  room_types: RoomTypeIn[];
  policies: PolicyIn[];
}

export interface HotelOut extends Required<Omit<HotelIn, "room_types" | "policies">> {
  id: number;
  created_at: string;
  room_types: (RoomTypeIn & { id: number })[];
  policies: (PolicyIn & { id: number; updated_at: string })[];
}

export interface HotelSummary {
  id: number;
  name: string;
  city: string | null;
  created_at: string;
}
