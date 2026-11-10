"""B routes consume the shared server-derived account scope."""
from app.api.deps import CurrentUser
from app.core.auth.scope import get_account_scope, authorize_profile_access, DENY_MESSAGE
from app.core.errors import BizError
from fastapi import Request


def require_manager(request: Request, user: CurrentUser):
    scope = get_account_scope(user)
    if not scope.can_view_all_persons:
        if scope.role!='hr' or scope.allowed_person_ids is None:
            raise BizError(403,DENY_MESSAGE)
        target=request.path_params.get('talent_id')
        if target is not None:
            if not authorize_profile_access(scope,int(target)).allowed:raise BizError(403,DENY_MESSAGE)
        elif request.url.path.rstrip('/') not in ('/api/talents','/api/talents/search','/api/talents/questions/batch'):
            raise BizError(403,DENY_MESSAGE)
    return scope


def require_profile_reader(talent_id: int, user: CurrentUser):
    scope = get_account_scope(user)
    if not authorize_profile_access(scope, talent_id).allowed:
        raise BizError(403, DENY_MESSAGE)
    return scope
