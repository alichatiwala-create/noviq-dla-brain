-- The CA (Current Awards) importer occasionally hits a NSN whose award was
-- already saved under a DIFFERENT NSN - this happens because one DLA
-- contract/delivery order can legitimately cover multiple different NSN
-- line items at once. The old UNIQUE (award_number) rule only allowed that
-- award number to be saved ONCE, total, across the whole table - so the
-- second (and any further) NSN sharing that same award silently couldn't
-- be saved, even though it's real data for that NSN too.
--
-- This widens the rule to UNIQUE (nsn, award_number) instead: the same
-- award can now be recorded once PER NSN it actually applies to, while
-- still preventing the same NSN from getting the same award saved twice.

ALTER TABLE dla_award_history DROP CONSTRAINT IF EXISTS dla_award_history_award_number_key;
ALTER TABLE dla_award_history ADD CONSTRAINT dla_award_history_nsn_award_number_key UNIQUE (nsn, award_number);
