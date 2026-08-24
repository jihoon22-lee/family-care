import type { FamilyMemberResponse } from "../../api/generated";

export function FamilyMemberPicker({
  members,
  selectedId,
  onChange,
}: {
  members: FamilyMemberResponse[];
  selectedId: string;
  onChange: (memberId: string) => void;
}) {
  return (
    <label className="member-picker">
      <span>가족 구성원</span>
      <select
        value={selectedId}
        onChange={(event) => onChange(event.target.value)}
      >
        {members.map((member) => (
          <option key={member.id} value={member.id}>
            {member.display_name}
          </option>
        ))}
      </select>
    </label>
  );
}
