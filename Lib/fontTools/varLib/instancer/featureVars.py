
from fontTools.varLib.models import normalizeValue
from copy import deepcopy
import logging


log = logging.getLogger("fontTools.varLib.instancer.featureVars")


def _featureVariationRecordIsUnique(rec, seen):
    conditionSet = []
    for cond in rec.ConditionSet.ConditionTable:
        if cond.Format != 1:
            # can't tell whether this is duplicate, assume is unique
            return True
        conditionSet.append(
            (cond.AxisIndex, cond.FilterRangeMinValue, cond.FilterRangeMaxValue)
        )
    # besides the set of conditions, we also include the FeatureTableSubstitution
    # version to identify unique FeatureVariationRecords, even though only one
    # version is currently defined. It's theoretically possible that multiple
    # records with same conditions but different substitution table version be
    # present in the same font for backward compatibility.
    recordKey = frozenset([rec.FeatureTableSubstitution.Version] + conditionSet)
    if recordKey in seen:
        return False
    else:
        seen.add(recordKey)  # side effect
        return True


def _limitFeatureVariationConditionRange(condition, axisLimit):
    minValue = condition.FilterRangeMinValue
    maxValue = condition.FilterRangeMaxValue

    if (
        minValue > maxValue
        or minValue > axisLimit.maximum
        or maxValue < axisLimit.minimum
    ):
        # condition invalid or out of range
        return

    return tuple(normalizeValue(v, axisLimit) for v in (minValue, maxValue))


def _instantiateFeatureVariationRecord(
    record, recIdx, axisLimits, fvarAxes, axisIndexMap
):
    applies = True
    newConditions = []
    from fontTools.varLib.instancer import NormalizedAxisTriple
    default_triple = NormalizedAxisTriple(-1, 0, +1)
    for i, condition in enumerate(record.ConditionSet.ConditionTable):
        if condition.Format == 1:
            axisIdx = condition.AxisIndex
            axisTag = fvarAxes[axisIdx].axisTag

            minValue = condition.FilterRangeMinValue
            maxValue = condition.FilterRangeMaxValue
            triple = axisLimits.get(axisTag, default_triple)
            if not (minValue <= triple.default <= maxValue):
                applies = False
                # condition not met so remove entire record
                if triple.minimum > maxValue or triple.maximum < minValue:
                    newConditions = None
                    break

            if axisTag in axisIndexMap:
                # remap axis index
                condition.AxisIndex = axisIndexMap[axisTag]
                newConditions.append(condition)
        else:
            log.warning(
                "Condition table {0} of FeatureVariationRecord {1} has "
                "unsupported format ({2}); ignored".format(i, recIdx, condition.Format)
            )
            applies = False
            newConditions.append(condition)

    if newConditions:
        record.ConditionSet.ConditionTable = newConditions
        shouldKeep = True
    else:
        shouldKeep = False

    return applies, shouldKeep


def _limitFeatureVariationRecord(record, axisLimits, axisOrder):
    newConditions = []
    for condition in record.ConditionSet.ConditionTable:
        if condition.Format == 1:
            axisIdx = condition.AxisIndex
            axisTag = axisOrder[axisIdx]
            if axisTag in axisLimits:
                axisLimit = axisLimits[axisTag]
                newRange = _limitFeatureVariationConditionRange(condition, axisLimit)
                if newRange:
                    # keep condition with updated limits
                    minimum, maximum = newRange
                    condition.FilterRangeMinValue = minimum
                    condition.FilterRangeMaxValue = maximum
                    if minimum != -1 or maximum != +1:
                        newConditions.append(condition)
                else:
                    # condition out of range, remove entire record
                    newConditions = None
                    break
            else:
                newConditions.append(condition)
        else:
            newConditions.append(condition)

    record.ConditionSet.ConditionTable = newConditions
    return newConditions is not None


def _instantiateFeatureVariations(table, fvarAxes, axisLimits):
    pinnedAxes = set(axisLimits.pinnedLocation())
    axisOrder = [axis.axisTag for axis in fvarAxes if axis.axisTag not in pinnedAxes]
    axisIndexMap = {axisTag: axisOrder.index(axisTag) for axisTag in axisOrder}

    featureVariationApplied = False
    uniqueRecords = set()
    newRecords = []

    for i, record in enumerate(table.FeatureVariations.FeatureVariationRecord):
        applies, shouldKeep = _instantiateFeatureVariationRecord(
            record, i, axisLimits, fvarAxes, axisIndexMap
        )
        if shouldKeep:
            shouldKeep = _limitFeatureVariationRecord(record, axisLimits, axisOrder)

        if shouldKeep and _featureVariationRecordIsUnique(record, uniqueRecords):
            newRecords.append(record)

        if applies and not featureVariationApplied:
            assert record.FeatureTableSubstitution.Version == 0x00010000
            for rec in record.FeatureTableSubstitution.SubstitutionRecord:
                table.FeatureList.FeatureRecord[rec.FeatureIndex].Feature = deepcopy(
                    rec.Feature
                )
            # Set variations only once
            featureVariationApplied = True

    if newRecords:
        table.FeatureVariations.FeatureVariationRecord = newRecords
        table.FeatureVariations.FeatureVariationCount = len(newRecords)
    else:
        del table.FeatureVariations
        # downgrade table version if there are no FeatureVariations left
        table.Version = 0x00010000


def instantiateFeatureVariations(varfont, axisLimits):
    for tableTag in ("GPOS", "GSUB"):
        if tableTag not in varfont or not getattr(
            varfont[tableTag].table, "FeatureVariations", None
        ):
            continue
        log.info("Instantiating FeatureVariations of %s table", tableTag)
        _instantiateFeatureVariations(
            varfont[tableTag].table, varfont["fvar"].axes, axisLimits
        )
        # remove unreferenced lookups
        varfont[tableTag].prune_lookups()

