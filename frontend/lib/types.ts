import { z } from "zod";

export const organizationSchema = z.object({
  id: z.string().uuid(),
  name: z.string().min(1),
  slug: z.string().min(1),
  created_at: z.string(),
});

export type Organization = z.infer<typeof organizationSchema>;
