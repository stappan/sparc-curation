import rdflib
from types import GeneratorType
from pyontutils import combinators as cmb
from pyontutils.namespaces import TEMP, isAbout
from pyontutils.closed_namespaces import rdf, rdfs, owl, dc
from scibot.extract import normalizeDoi
from pysercomb.pyr.units import Expr, _Quant as Quantity, Range
from sparcur import datasets as dat
from sparcur.core import OntId, OntTerm
from sparcur.utils import log, logd, sparc
from sparcur.protocols import ProtocolData

a = rdf.type

class TripleConverter(dat.HasErrors):
    # TODO consider putting mappings in a dict hierarchy
    # that reflects where they are in the schema??
    known_skipped = tuple()
    mapping = tuple()

    class Extra:
        def __init__(self, converter):
            self.c = converter
            self.integrator = converter.integrator

    @classmethod
    def setup(cls):
        for attr, predicate in cls.mapping:
            def _func(self, value, p=predicate): return p, self.l(value)
            setattr(cls, attr, _func)

    def __init__(self, json_source, integrator=None):
        """ in case we want to do contextual things here """
        super().__init__()
        self._source = json_source
        self.integrator = integrator
        self.extra = self.Extra(self)

    def l(self, value):
        if isinstance(value, OntId):
            return value.u
        if isinstance(value, Expr):
            return value
        if isinstance(value, Quantity):
            return value
        elif isinstance(value, str) and value.startswith('http'):
            return OntId(value).u
        elif isinstance(value, dict):  # FIXME this is too late to convert?
            # NOPE! This idiot put a type field in his json dicts!
            if 'type' in value:
                if value['type'] == 'quantity':
                    return Quantity.fromJson(value)
                elif value['type'] == 'range':
                    return Range.fromJson(value)

            raise ValueError(value)
        else:
            return rdflib.Literal(value)

    def triples_gen(self, subject):
        if not isinstance(subject, rdflib.URIRef):
            subject = rdflib.URIRef(subject)

        for field, value in self._source.items():
            #log.debug(f'{field}: {value}')
            if type(field) is object:
                continue  # the magic helper key for Pipeline
            convert = getattr(self, field, None)
            extra = getattr(self.extra, field, None)
            if convert is not None:
                if isinstance(value, tuple) or isinstance(value, list):
                    values = value
                else:
                    values = value,

                for v in values:
                    log.debug(f'{field} {v} {convert}')
                    p, o = convert(v)
                    log.debug(o)
                    if isinstance(o, Expr) or isinstance(o, Quantity):
                        s = rdflib.BNode()
                        yield subject, p, s
                        qt = sparc.Measurement
                        if isinstance(o, Range):
                            yield from o.asRdf(s, quantity_rdftype=qt)
                        elif isinstance(o, Quantity):
                            yield from o.asRdf(s, rdftype=qt)
                        else:
                            log.warning(f'unhanded Expr type {o}')
                            yield from o.asRdf(s)
                    else:
                        yield subject, p, o

                    if extra is not None:
                        yield from extra(v)

            elif field in self.known_skipped:
                pass

            else:
                msg = f'Unhandled {self.__class__.__name__} field: {field}'
                log.warning(msg)
                self.addError(msg, pipeline_stage=self.__class__.__name__ + '.export-error')


class ContributorConverter(TripleConverter):
    known_skipped = 'id', 'name'
    mapping = (
        ('first_name', sparc.firstName),
        ('middle_name', TEMP.middleName),
        ('last_name', sparc.lastName),
        ('contributor_affiliation', TEMP.hasAffiliation),
        ('is_contact_person', sparc.isContactPerson),
        ('is_responsible_pi', sparc.isContactPerson),
        ('blackfynn_user_id', TEMP.hasBlackfynnUserId),
        ('contributor_orcid_id', sparc.hasORCIDId),
        )
 
    def contributor_role(self, value):
        return TEMP.hasRole, TEMP[value]

ContributorConverter.setup()


class MetaConverter(TripleConverter):
    mapping = [
        ['acknowledgements', TEMP.acknowledgements],
        ['folder_name', rdfs.label],
        ['title', dc.title],
        ['protocol_url_or_doi', TEMP.hasProtocol],
        #['award_number', TEMP.hasAwardNumber],
        ['species', isAbout],
        ['organ', isAbout],
        ['modality', TEMP.hasExperimentalModality],
        ['uri_api', TEMP.hasUriApi],
        ['uri_human', TEMP.hasUriHuman],
        ['keywords', isAbout],
        ['description', dc.description],
        ['dirs', TEMP.hasNumberOfDirectories],
        ['files', TEMP.hasNumberOfFiles],
        ['size', TEMP.hasSizeInBytes],
        ['subject_count', TEMP.hasNumberOfSubjects],
        ['sample_count', TEMP.hasNumberOfSamples],
        ['contributor_count', TEMP.hasNumberOfContributors],

        ['title_for_complete_data_set', TEMP.collectionTitle],
        ['prior_batch_number', TEMP.Continues],  # see datacite relationType

        ['originating_article_doi', TEMP.IsDescribedBy],  # see relationType

        # TODO
        #['additional_links', ],
        #['completeness_of_data_set', ],
        #['examples'],
        #['links'],

    ]

    def principal_investigator(self, value):
        index = int(value.rsplit('/', 1)[-1])
        id = self.integrator.data['contributors'][index]['id']
        return TEMP.hasResponsiblePrincialInvestigator, rdflib.URIRef(id)  # FIXME reload -> ir

    def award_number(self, value): return TEMP.hasAwardNumber, TEMP[f'awards/{value}']
    class Extra:
        def __init__(self, converter):
            self.c = converter
            self.integrator = converter.integrator

        def award_number(self, value):
            _, s = self.c.award_number(value)
            yield s, a, owl.NamedIndividual
            yield s, a, TEMP.FundedResearchProject

        def protocol_url_or_doi(self, value):
            _, s = self.c.protocol_url_or_doi(value)
            yield s, a, owl.NamedIndividual
            yield s, a, sparc.Protocol
            pj = ProtocolData(self.integrator.id)(value)  # FIXME a bit opaque, needs to move to a pipeline, clean up init etc.
            if pj:
                label = pj['protocol']['title']
                yield s, rdfs.label, rdflib.Literal(label)
                nsteps = len(pj['protocol']['steps'])
                yield s, TEMP.protocolHasNumberOfSteps, rdflib.Literal(nsteps)

            yield from self.integrator.triples_protcur(s)
MetaConverter.setup()  # box in so we don't forget


class DatasetConverter(TripleConverter):
    known_skipped = 'id', 'errors', 'inputs', 'subjects', 'meta', 'creators'
    mapping = []
DatasetConverter.setup()


class StatusConverter(TripleConverter):
    known_skipped = 'submission_errors', 'curation_errors'
    mapping = [
        ['submission_index', TEMP.submissionIndex],
        ['curation_index', TEMP.curationIndex],
        ['error_index', TEMP.errorIndex],
    ]
StatusConverter.setup()


class SubjectConverter(TripleConverter):
    known_skipped = 'subject_id',
    mapping = [
        ['age_category', TEMP.hasAgeCategory],
        ['species', sparc.animalSubjectIsOfSpecies],
        ['group', TEMP.hasAssignedGroup],
        #['rrid_for_strain', rdf.type],  # if only
        ['rrid_for_strain', sparc.specimenHasIdentifier],  # really subClassOf strain
        ['genus', sparc.animalSubjectIsOfGenus],
        ['species', sparc.animalSubjectIsOfSpecies],
        ['strain', sparc.animalSubjectIsOfStrain],
        ['weight', sparc.animalSubjectHasWeight],
        ['initial_weight', sparc.animalSubjectHasWeight],  # TODO time
        ['mass', sparc.animalSubjectHasWeight],
        ['body_mass', sparc.animalSubjectHasWeight],  # TODO
        ['sex', TEMP.hasBiologicalSex],
        ['gender', sparc.hasGender],
        ['age', TEMP.hasAge],
        ['stimulation_site', sparc.spatialLocationOfModulator],  # TODO ontology
        ['stimulator', sparc.stimulatorUtilized],
    ]
SubjectConverter.setup()


class ApiNATOMYConverter(TripleConverter):
    @staticmethod
    def apinatbase():
        # TODO move it external file
        yield TEMP.isAdvectivelyConnectedTo, a, owl.ObjectProperty
        yield TEMP.isAdvectivelyConnectedTo, a, owl.SymmetricProperty
        yield TEMP.isAdvectivelyConnectedTo, a, owl.TransitiveProperty

        yield TEMP.advectivelyConnects, a, owl.ObjectProperty
        yield TEMP.advectivelyConnects, a, owl.TransitiveProperty

        yield TEMP.advectivelyConnectsFrom, a, owl.ObjectProperty
        yield TEMP.advectivelyConnectsFrom, rdfs.subPropertyOf, TEMP.advectivelyConnects

        yield TEMP.advectivelyConnectsTo, a, owl.ObjectProperty
        yield TEMP.advectivelyConnectsTo, rdfs.subPropertyOf, TEMP.advectivelyConnects
        yield TEMP.advectivelyConnectsTo, owl.inverseOf, TEMP.advectivelyConnectsFrom

        idct = TEMP.isDiffusivelyConnectedTo
        yield idct, a, owl.ObjectProperty
        yield idct, a, owl.SymmetricProperty
        yield idct, a, owl.TransitiveProperty  # technically correct modulate the concentration gradient

        yield TEMP.diffusivelyConnects, a, owl.ObjectProperty
        yield TEMP.diffusivelyConnects, a, owl.TransitiveProperty

        yield TEMP.diffusivelyConnectsFrom, a, owl.ObjectProperty
        yield TEMP.diffusivelyConnectsFrom, rdfs.subPropertyOf, TEMP.advectivelyConnects

        yield TEMP.diffusivelyConnectsTo, a, owl.ObjectProperty
        yield TEMP.diffusivelyConnectsTo, rdfs.subPropertyOf, TEMP.advectivelyConnects
        yield TEMP.diffusivelyConnectsTo, owl.inverseOf, TEMP.advectivelyConnectsFrom

    def materialTriples(self, subject, link):
        rm = self._source

        def yield_from_id(s, matid, predicate=TEMP.advectivelyConnectsMaterial):
            mat = rm[matid]
            if 'external' in mat:
                mat_s = OntTerm(mat['external'][0])
                yield s, predicate, mat_s.u
                yield mat_s.u, a, owl.Class
                yield mat_s.u, rdfs.label, rdflib.Literal(mat_s.label)
                if 'materials' in mat:
                    for submat_id in mat['materials']:
                        yield from yield_from_id(mat_s, submat_id, TEMP.hasConstituent)

            else:
                log.warning(f'no external id for {mat}')

        for matid in link['conveyingMaterials']:
            yield from yield_from_id(subject, matid)

    @property
    def triples_gen(self):
        rm = self._source

        # FIXME there doesn't seem to be a section that tells me the name
        # of top level model so I have to know its name beforhand
        # the id is in the model, having the id in the resource map
        # prevents issues if these things get sent decoupled
        id = 'Urinary Omega Tree'

        links = rm[id]['links']

        st = []
        from_to = []
        ot = None
        yield from self.apinatbase()
        for link in links:
            if 'conveyingType' in link:
                if link['conveyingType'] == 'ADVECTIVE':
                    p_is =   TEMP.isAdvectivelyConnectedTo
                    p_from = TEMP.advectivelyConnectsFrom
                    p_to =   TEMP.advectivelyConnectsTo
                elif link['conveyingType'] == 'DIFFUSIVE':
                    p_is =   TEMP.isDiffusivelyConnectedTo
                    p_from = TEMP.diffusivelyConnectsFrom
                    p_to =   TEMP.diffusivelyConnectsTo
                else:
                    log.critical(f'unhandled conveying type {link}')
                    continue

                source = link['source']
                target = link['target']
                ok = True
                if len(from_to) == 2:  # otherwise
                    st = []
                    from_to = []
                for i, e in enumerate((source, target)):
                    ed = rm[e]
                    if 'external' not in ed:
                        if not i and from_to:
                            # TODO make sure the intermediate ids match
                            pass
                        else:
                            ok = False
                            break
                    else:
                        st.append(e)
                        from_to.append(OntId(ed['external'][0]))

                conveying = link['conveyingLyph']
                cd = rm[conveying]
                if 'external' in cd:
                    old_ot = ot
                    ot = OntTerm(cd['external'][0])
                    yield ot.u, a, owl.Class
                    yield ot.u, TEMP.internalId, rdflib.Literal(conveying)
                    yield ot.u, rdfs.label, rdflib.Literal(ot.label)

                    yield from self.materialTriples(ot.u, link)  # FIXME locate this correctly

                    if ok:
                        u, d = from_to
                        if st[0] == source:
                            yield u, rdfs.label, rdflib.Literal(OntTerm(u).label)
                            yield u, a, owl.Class
                            yield from cmb.restriction.serialize(ot.u, p_from, u)

                        if st[1] == target:
                            yield d, rdfs.label, rdflib.Literal(OntTerm(d).label)
                            yield d, a, owl.Class
                            yield from cmb.restriction.serialize(ot.u, p_to, d)

                    elif old_ot != ot:
                        yield from cmb.restriction.serialize(ot.u, p_from, old_ot.u)

                if not ok:
                    logd.info(f'{source} {target} issue')
                    continue

                for inid, e in zip(st, from_to):
                    yield e.u, a, owl.Class
                    yield e.u, rdfs.label, rdflib.Literal(OntTerm(e).label)
                    yield e.u, TEMP.internalId, rdflib.Literal(inid)

                f, t = from_to
                yield from cmb.restriction.serialize(f.u, p_is, t.u)

ApiNATOMYConverter.setup()
