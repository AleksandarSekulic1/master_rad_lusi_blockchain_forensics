import { UserRole } from '../../core/models/shared.models';

export interface CreateUserRequest {
  username: string;
  password: string;
  role: UserRole;
}

export interface ResetLinkResponse {
  reset_link: string;
  token: string;
}
