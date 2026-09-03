// Shared E.164 validation used by every dedicated-calling-number input
// (organizations/new-org-dialog.tsx, organizations/edit-org-dialog.tsx,
// organizations/phone-number-list-field.tsx).
export const E164_REGEX = /^\+\d{8,15}$/;
export const E164_MESSAGE = "Enter a valid E.164 number, e.g. +919876543210";
