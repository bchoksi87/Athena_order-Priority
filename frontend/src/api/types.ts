/**
 * Hand-written TypeScript mirror of the backend domain model
 * (backend/app/domain/{enums,models,results,config}.py). Field names are
 * snake_case exactly as serialised by the API. Datetimes are ISO-8601 UTC
 * strings. Durations are minutes unless the field name says hours.
 */

export * from "./types/enums";
export * from "./types/models";
export * from "./types/results";
export * from "./types/config";
export * from "./types/api";
