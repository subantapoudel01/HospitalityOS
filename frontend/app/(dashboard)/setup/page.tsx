"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Section } from "@/components/ui/Section";
import {
  ApiError,
  createHotel,
  getHotel,
  listHotels,
  updateHotel,
  type FieldErrors,
} from "@/lib/api";
import { assessEntry } from "@/lib/knowledgeQuality";
import {
  POLICY_CATEGORIES,
  POLICY_LABELS,
  type HotelIn,
  type PolicyCategory,
} from "@/lib/types";

/** Form-local shapes: everything is a string while being typed. */
interface RoomTypeForm {
  name: string;
  description: string;
  base_rate: string;
  max_occupancy: string;
  amenities: string;
}

interface PolicyForm {
  category: PolicyCategory;
  content_text: string;
}

const blankRoomType = (): RoomTypeForm => ({
  name: "",
  description: "",
  base_rate: "",
  max_occupancy: "2",
  amenities: "",
});

const blankPolicy = (): PolicyForm => ({
  category: "checkin_checkout",
  content_text: "",
});

export default function SetupPage() {
  const [hotelId, setHotelId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});

  const [profile, setProfile] = useState({
    name: "",
    description: "",
    city: "",
    address: "",
    phone: "",
    whatsapp_number: "",
  });
  const [roomTypes, setRoomTypes] = useState<RoomTypeForm[]>([blankRoomType()]);
  const [policies, setPolicies] = useState<PolicyForm[]>([blankPolicy()]);

  // Single property per tenant for the pilot (UI_UX_PLAN: no multi-property
  // switcher), so load the first hotel if one already exists and edit it.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const hotels = await listHotels();
        if (cancelled || hotels.length === 0) return;
        const hotel = await getHotel(hotels[0].id);
        if (cancelled) return;
        setHotelId(hotel.id);
        setProfile({
          name: hotel.name ?? "",
          description: hotel.description ?? "",
          city: hotel.city ?? "",
          address: hotel.address ?? "",
          phone: hotel.phone ?? "",
          whatsapp_number: hotel.whatsapp_number ?? "",
        });
        if (hotel.room_types.length) {
          setRoomTypes(
            hotel.room_types.map((rt) => ({
              name: rt.name,
              description: rt.description ?? "",
              base_rate: String(rt.base_rate),
              max_occupancy: String(rt.max_occupancy),
              amenities: (rt.amenities ?? []).join(", "),
            }))
          );
        }
        if (hotel.policies.length) {
          setPolicies(
            hotel.policies.map((p) => ({
              category: p.category,
              content_text: p.content_text,
            }))
          );
        }
      } catch (err) {
        if (!cancelled) {
          setFormError(
            err instanceof ApiError
              ? err.message
              : "Could not load existing setup."
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function updateRoomType(i: number, patch: Partial<RoomTypeForm>) {
    setRoomTypes((prev) =>
      prev.map((rt, idx) => (idx === i ? { ...rt, ...patch } : rt))
    );
  }

  function updatePolicy(i: number, patch: Partial<PolicyForm>) {
    setPolicies((prev) =>
      prev.map((p, idx) => (idx === i ? { ...p, ...patch } : p))
    );
  }

  /** Client-side checks, so obvious mistakes never reach the network. */
  function validate(): FieldErrors {
    const e: FieldErrors = {};
    if (!profile.name.trim()) e["name"] = "Resort name is required.";

    roomTypes.forEach((rt, i) => {
      const touched =
        rt.name.trim() || rt.base_rate.trim() || rt.description.trim();
      if (!touched) return; // fully blank rows are dropped, not flagged
      if (!rt.name.trim()) {
        e["room_types." + i + ".name"] = "Room type name is required.";
      }
      if (!rt.base_rate.trim()) {
        e["room_types." + i + ".base_rate"] = "Rate is required.";
      } else if (!/^\d+(\.\d{1,2})?$/.test(rt.base_rate.trim())) {
        e["room_types." + i + ".base_rate"] =
          "Enter a number, up to 2 decimal places.";
      }
      const occ = Number(rt.max_occupancy);
      if (!Number.isInteger(occ) || occ < 1) {
        e["room_types." + i + ".max_occupancy"] = "Must be at least 1.";
      }
    });

    return e;
  }

  function buildPayload(): HotelIn {
    return {
      name: profile.name.trim(),
      description: profile.description.trim() || null,
      city: profile.city.trim() || null,
      address: profile.address.trim() || null,
      phone: profile.phone.trim() || null,
      whatsapp_number: profile.whatsapp_number.trim() || null,
      room_types: roomTypes
        .filter((rt) => rt.name.trim() && rt.base_rate.trim())
        .map((rt) => ({
          name: rt.name.trim(),
          description: rt.description.trim() || null,
          base_rate: rt.base_rate.trim(),
          max_occupancy: Number(rt.max_occupancy) || 1,
          amenities: rt.amenities
            .split(",")
            .map((a) => a.trim())
            .filter(Boolean),
        })),
      policies: policies
        .filter((p) => p.content_text.trim())
        .map((p) => ({
          category: p.category,
          content_text: p.content_text.trim(),
        })),
    };
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    setSaved(null);
    setFormError(null);

    const clientErrors = validate();
    setErrors(clientErrors);
    if (Object.keys(clientErrors).length > 0) {
      setFormError("Please fix the highlighted fields.");
      return;
    }

    setSaving(true);
    try {
      const payload = buildPayload();
      const result = hotelId
        ? await updateHotel(hotelId, payload)
        : await createHotel(payload);
      setHotelId(result.id);
      setErrors({});
      setSaved(
        "Saved " +
          result.name +
          " with " +
          result.room_types.length +
          " room type(s) and " +
          result.policies.length +
          " policy entries."
      );
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fieldErrors);
        setFormError(err.message);
      } else {
        setFormError("Something went wrong while saving.");
      }
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p>Loading setup...</p>;

  return (
    <form onSubmit={handleSubmit} noValidate>
      <h1 className="page-title">Resort setup</h1>
      <p className="page-sub">
        {hotelId
          ? "Editing your saved property. Saving replaces the room types and policies below."
          : "Set up your property so the AI receptionist can answer guest questions accurately."}
      </p>

      {formError && (
        <div className="banner banner-error" role="alert">
          {formError}
        </div>
      )}
      {saved && (
        <div className="banner banner-ok" role="status">
          {saved}
        </div>
      )}

      <Section title="Property profile" hint="Basic details guests ask about.">
        <Field label="Resort name" htmlFor="name" required error={errors["name"]}>
          <input
            id="name"
            value={profile.name}
            aria-invalid={!!errors["name"]}
            onChange={(e) => setProfile({ ...profile, name: e.target.value })}
          />
        </Field>

        <Field
          label="Description"
          htmlFor="description"
          hint="A short description of the property. The AI uses this when introducing your resort."
          error={errors["description"]}
        >
          <textarea
            id="description"
            value={profile.description}
            onChange={(e) =>
              setProfile({ ...profile, description: e.target.value })
            }
          />
        </Field>

        <div className="row">
          <Field label="City" htmlFor="city" error={errors["city"]}>
            <input
              id="city"
              value={profile.city}
              onChange={(e) => setProfile({ ...profile, city: e.target.value })}
            />
          </Field>
          <Field label="Phone" htmlFor="phone" error={errors["phone"]}>
            <input
              id="phone"
              value={profile.phone}
              onChange={(e) => setProfile({ ...profile, phone: e.target.value })}
            />
          </Field>
        </div>

        <Field label="Address" htmlFor="address" error={errors["address"]}>
          <input
            id="address"
            value={profile.address}
            onChange={(e) => setProfile({ ...profile, address: e.target.value })}
          />
        </Field>

        <Field
          label="WhatsApp number"
          htmlFor="whatsapp"
          hint="Used later when the WhatsApp channel is connected."
          error={errors["whatsapp_number"]}
        >
          <input
            id="whatsapp"
            value={profile.whatsapp_number}
            onChange={(e) =>
              setProfile({ ...profile, whatsapp_number: e.target.value })
            }
          />
        </Field>
      </Section>

      <Section
        title="Room types and rates"
        hint="Rates are per night in NPR. Add one entry per type of room, not per room."
      >
        {roomTypes.map((rt, i) => (
          <div className="entry" key={i}>
            <div className="entry-head">
              <span className="entry-label">Room type {i + 1}</span>
              {roomTypes.length > 1 && (
                <Button
                  type="button"
                  variant="link"
                  onClick={() =>
                    setRoomTypes(roomTypes.filter((_, idx) => idx !== i))
                  }
                >
                  Remove
                </Button>
              )}
            </div>

            <div className="row-3">
              <Field
                label="Name"
                htmlFor={"rt-name-" + i}
                required
                error={errors["room_types." + i + ".name"]}
              >
                <input
                  id={"rt-name-" + i}
                  value={rt.name}
                  placeholder="Deluxe Lake View"
                  aria-invalid={!!errors["room_types." + i + ".name"]}
                  onChange={(e) => updateRoomType(i, { name: e.target.value })}
                />
              </Field>

              <Field
                label="Rate per night"
                htmlFor={"rt-rate-" + i}
                required
                error={errors["room_types." + i + ".base_rate"]}
              >
                <div className="rate-prefix">
                  <span>NPR</span>
                  <input
                    id={"rt-rate-" + i}
                    inputMode="decimal"
                    value={rt.base_rate}
                    placeholder="4500.00"
                    aria-invalid={!!errors["room_types." + i + ".base_rate"]}
                    onChange={(e) =>
                      updateRoomType(i, { base_rate: e.target.value })
                    }
                  />
                </div>
              </Field>

              <Field
                label="Max guests"
                htmlFor={"rt-occ-" + i}
                error={errors["room_types." + i + ".max_occupancy"]}
              >
                <input
                  id={"rt-occ-" + i}
                  type="number"
                  min={1}
                  value={rt.max_occupancy}
                  aria-invalid={!!errors["room_types." + i + ".max_occupancy"]}
                  onChange={(e) =>
                    updateRoomType(i, { max_occupancy: e.target.value })
                  }
                />
              </Field>
            </div>

            <Field label="Description" htmlFor={"rt-desc-" + i}>
              <input
                id={"rt-desc-" + i}
                value={rt.description}
                placeholder="Balcony facing the lake"
                onChange={(e) =>
                  updateRoomType(i, { description: e.target.value })
                }
              />
            </Field>

            <Field
              label="Amenities"
              htmlFor={"rt-am-" + i}
              hint="Separate with commas, for example: AC, Wi-Fi, Hot water"
            >
              <input
                id={"rt-am-" + i}
                value={rt.amenities}
                onChange={(e) => updateRoomType(i, { amenities: e.target.value })}
              />
            </Field>
          </div>
        ))}

        <Button
          type="button"
          onClick={() => setRoomTypes([...roomTypes, blankRoomType()])}
        >
          + Add room type
        </Button>
      </Section>

      <Section
        title="Policies"
        hint="Guests ask these constantly. Write them the way you would say them."
      >
        {policies.map((p, i) => (
          <div className="entry" key={i}>
            <div className="entry-head">
              <span className="entry-label">Policy {i + 1}</span>
              {policies.length > 1 && (
                <Button
                  type="button"
                  variant="link"
                  onClick={() =>
                    setPolicies(policies.filter((_, idx) => idx !== i))
                  }
                >
                  Remove
                </Button>
              )}
            </div>

            <Field label="Category" htmlFor={"p-cat-" + i}>
              <select
                id={"p-cat-" + i}
                value={p.category}
                onChange={(e) =>
                  updatePolicy(i, {
                    category: e.target.value as PolicyCategory,
                  })
                }
              >
                {POLICY_CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {POLICY_LABELS[c]}
                  </option>
                ))}
              </select>
            </Field>

            <Field
              label="Policy"
              htmlFor={"p-text-" + i}
              error={errors["policies." + i + ".content_text"]}
            >
              <textarea
                id={"p-text-" + i}
                value={p.content_text}
                placeholder="Check-in from 2 PM, check-out by 11 AM."
                aria-invalid={!!errors["policies." + i + ".content_text"]}
                onChange={(e) =>
                  updatePolicy(i, { content_text: e.target.value })
                }
              />
              {/* Warn while the field is still on screen. A policy reading
                  "10/12" was accepted once and the assistant went on to
                  state "Check-out is at 12:00 pm" as fact. */}
              {assessEntry(p.content_text, "This policy").map((w) => (
                <p
                  key={w.code}
                  className={"quality quality-" + w.severity}
                  role={w.severity === "high" ? "alert" : undefined}
                >
                  {w.severity === "high" && <strong>Guests may get a wrong answer. </strong>}
                  {w.message}
                </p>
              ))}
            </Field>
          </div>
        ))}

        <Button
          type="button"
          onClick={() => setPolicies([...policies, blankPolicy()])}
        >
          + Add policy
        </Button>
      </Section>

      <div className="actions">
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Saving..." : hotelId ? "Save changes" : "Create property"}
        </Button>
        <span className="section-hint">
          Empty room type and policy rows are ignored.
        </span>
      </div>
    </form>
  );
}
