package org.twak.tweed.gen;

import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import javax.swing.SwingUtilities;
import javax.vecmath.Matrix4d;
import javax.vecmath.Point3d;
import javax.vecmath.Vector3d;

import org.twak.readTrace.MiniTransform;
import org.twak.tweed.Tweed;
import org.twak.tweed.TweedSettings;
import org.twak.utils.collections.Loop;
import org.twak.utils.collections.LoopL;
import org.twak.utils.collections.SuperLoop;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * Runs the on-demand satellite/mesh bridge without blocking Swing's EDT.
 *
 * The bridge is deliberately treated as an all-or-nothing producer. No mesh is
 * returned to the GUI until all three BlockGen OBJ files and a loadable
 * MiniMesh have been verified.
 */
final class SelectedBlockMeshService {
	static final String PIPELINE_CONTRACT_VERSION = "osm-prealign-v1";
	static final String BIG_IMAGE_PIPELINE_CONTRACT_VERSION = "big-image-app192-v3-vertex-colour";
	static final String BUILDING_PUBLICATION_VERSION = "per-footprint-v2";
	static final String BUILDINGS_INDEX_KIND = "myProject.selection.buildings";
	static final long ROOF_BACKFILL_TIMEOUT_SECONDS = 120;

	enum ModelSource {
		SATELLITE_TILES( "satellite tiles", "", PIPELINE_CONTRACT_VERSION, true, 7200 ),
		BIG_IMAGE( "big image", "big-image-", BIG_IMAGE_PIPELINE_CONTRACT_VERSION, false, 7200 );

		final String label;
		final String selectionPrefix;
		final String pipelineContractVersion;
		final boolean osmPrealign;
		final long timeoutSeconds;

		ModelSource( String label, String selectionPrefix, String pipelineContractVersion,
				boolean osmPrealign, long timeoutSeconds ) {
			this.label = label;
			this.selectionPrefix = selectionPrefix;
			this.pipelineContractVersion = pipelineContractVersion;
			this.osmPrealign = osmPrealign;
			this.timeoutSeconds = timeoutSeconds;
		}
	}

	interface Callback {
		void statusChanged( String status );
		void completed( Result result );
		void failed( Failure failure );
	}

	static final class Result {
		final String selectionId;
		final File outputDirectory;
		final File logFile;
		final File resultFile;
		final LoopL<Point3d> footprints;
		final List<BuildingResult> buildings;
		final ModelSource modelSource;
		final String pipelineContractVersion;
		final boolean osmPrealign;

		Result( Request request, List<BuildingResult> buildings ) {
			this.selectionId = request.selectionId;
			this.outputDirectory = request.outputDirectory;
			this.logFile = request.logFile;
			this.resultFile = request.resultFile;
			this.footprints = request.footprints;
			this.buildings = Collections.unmodifiableList( new ArrayList<>( buildings ) );
			this.modelSource = request.modelSource;
			this.pipelineContractVersion = request.pipelineContractVersion;
			this.osmPrealign = request.osmPrealign;
		}
	}

	/** A validated, independently loadable building published by the bridge. */
	static final class BuildingResult {
		final String id;
		final String componentId;
		final String footprintId;
		final List<String> footprintIds;
		final String sourceFeatureId;
		final String status;
		final File directory;
		final File miniMeshDirectory;
		final File croppedObj;
		final File gisObj;
		final File gisFootprintsObj;
		final LoopL<Point3d> footprints;
		final VertexColorObjGen.ParsedObj vertexColorObj;
		final ModelSource modelSource;

		BuildingResult( PublishedBuilding published, File miniMeshDirectory, LoopL<Point3d> footprints,
				VertexColorObjGen.ParsedObj vertexColorObj, ModelSource modelSource ) {
			this.id = published.id;
			this.componentId = published.componentId;
			this.footprintId = published.footprintId;
			this.footprintIds = Collections.unmodifiableList( new ArrayList<>( published.footprintIds ) );
			this.sourceFeatureId = published.sourceFeatureId;
			this.status = published.status;
			this.directory = published.directory;
			this.miniMeshDirectory = miniMeshDirectory;
			this.croppedObj = published.croppedObj;
			this.gisObj = published.gisObj;
			this.gisFootprintsObj = published.gisFootprintsObj;
			this.footprints = footprints;
			this.vertexColorObj = vertexColorObj;
			this.modelSource = modelSource;
		}

		String displayName() {
			String label = sourceFeatureId == null || sourceFeatureId.trim().isEmpty()
					? footprintId : sourceFeatureId;
			return modelSource.label + " building " + label;
		}
	}

	private static final class PublishedBuilding {
		final String id;
		final String componentId;
		final String footprintId;
		final List<String> footprintIds;
		final String sourceFeatureId;
		final String status;
		final File directory;
		final File croppedObj;
		final File gisObj;
		final File gisFootprintsObj;

		PublishedBuilding( String id, String componentId, String footprintId,
				List<String> footprintIds, String sourceFeatureId, String status, File directory,
				File croppedObj, File gisObj, File gisFootprintsObj ) {
			this.id = id;
			this.componentId = componentId;
			this.footprintId = footprintId;
			this.footprintIds = footprintIds;
			this.sourceFeatureId = sourceFeatureId;
			this.status = status;
			this.directory = directory;
			this.croppedObj = croppedObj;
			this.gisObj = gisObj;
			this.gisFootprintsObj = gisFootprintsObj;
		}
	}

	private static final class BuildingPublication {
		final List<PublishedBuilding> loadable;
		final Set<String> allFootprintIds;

		BuildingPublication( List<PublishedBuilding> loadable, Set<String> allFootprintIds ) {
			this.loadable = loadable;
			this.allFootprintIds = allFootprintIds;
		}
	}

	static final class Failure {
		final String reason;
		final Integer exitCode;
		final File logFile;
		final File resultFile;

		Failure( String reason, Integer exitCode, File logFile, File resultFile ) {
			this.reason = reason;
			this.exitCode = exitCode;
			this.logFile = logFile;
			this.resultFile = resultFile;
		}

		String details() {
			StringBuilder out = new StringBuilder( reason == null ? "unknown failure" : reason );
			if ( exitCode != null )
				out.append( "\nExit code: " ).append( exitCode );
			appendFileDetails( out, "Result", resultFile, 5000 );
			appendFileDetails( out, "Log", logFile, 5000 );
			return out.toString();
		}
	}

	private static final class Request {
		final String selectionId;
		final File workspace;
		final File outputDirectory;
		final File requestFile;
		final File logFile;
		final File resultFile;
		final LoopL<Point3d> footprints;
		final Map<String, Loop<Point3d>> footprintsById;
		final Map<String, Object> json;
		final ModelSource modelSource;
		final String pipelineContractVersion;
		final boolean osmPrealign;

		Request( File workspace, LoopL<Point3d> footprints, ModelSource modelSource ) throws IOException {
			this.workspace = workspace.getCanonicalFile();
			this.footprints = footprints;
			this.modelSource = modelSource;
			this.pipelineContractVersion = modelSource.pipelineContractVersion;
			this.osmPrealign = modelSource.osmPrealign;
			this.selectionId = modelSource.selectionPrefix + stableSelectionId( footprints );
			this.outputDirectory = new File( new File( this.workspace, "generated_blocks" ), selectionId );
			this.requestFile = new File( outputDirectory, "request.json" );
			this.logFile = new File( new File( new File( this.workspace, "logs" ), "selected-mesh" ),
					selectionId + ".log" );
			this.resultFile = new File( outputDirectory, "result.json" );

			this.footprintsById = new LinkedHashMap<>();
			List<Map<String, Object>> footprintJson = new ArrayList<>();
			for ( Loop<Point3d> loop : footprints ) {
				Map<String, Object> item = new LinkedHashMap<>();
				String canonical = canonicalLoop( loop );
				String footprintId = "footprint-" + sha256( canonical ).substring( 0, 12 );
				if ( footprintsById.put( footprintId, loop ) != null )
					throw new IOException( "Duplicate stable footprint id: " + footprintId );
				item.put( "id", footprintId );
				List<List<Double>> points = new ArrayList<>();
				for ( Point3d point : loop ) {
					List<Double> pair = new ArrayList<>( 2 );
					pair.add( point.x );
					pair.add( point.z );
					points.add( pair );
				}
				item.put( "points", points );
				footprintJson.add( item );
			}

			Map<String, Object> options = new LinkedHashMap<>();
			options.put( "block_mesh_padding", TweedSettings.settings.blockMeshPadding );
			options.put( "require_complete_buildings", true );
			options.put( "pipeline_contract_version", pipelineContractVersion );
			options.put( "osm_prealign", osmPrealign );
			options.put( "model_source", modelSource == ModelSource.BIG_IMAGE ? "big_image" : "satellite_tiles" );

			json = new LinkedHashMap<>();
			json.put( "workspace", this.workspace.getAbsolutePath() );
			json.put( "selection_id", selectionId );
			json.put( "footprints", footprintJson );
			json.put( "options", options );
		}
	}

	private final Tweed tweed;
	private final ExecutorService executor;
	private final Set<String> running = Collections.newSetFromMap( new ConcurrentHashMap<String, Boolean>() );

	SelectedBlockMeshService( Tweed tweed ) {
		this.tweed = tweed;
		this.executor = Executors.newSingleThreadExecutor( runnable -> {
			Thread thread = new Thread( runnable, "selected-satellite-mesh" );
			thread.setDaemon( true );
			return thread;
		} );
	}

	void submit( LoopL<Point3d> source, Callback callback ) {
		submit( source, ModelSource.SATELLITE_TILES, callback );
	}

	void submitBigImage( LoopL<Point3d> source, Callback callback ) {
		submit( source, ModelSource.BIG_IMAGE, callback );
	}

	private void submit( LoopL<Point3d> source, ModelSource modelSource, Callback callback ) {
		final LoopL<Point3d> footprints = deepCopy( source );
		final Request request;
		try {
			request = new Request( new File( Tweed.DATA ), footprints, modelSource );
		} catch ( Throwable error ) {
			deliverFailure( callback, new Failure(
					"Unable to prepare the selected footprint request: " + usefulMessage( error ),
					null, null, null ) );
			return;
		}

		if ( !running.add( request.selectionId ) ) {
			deliverStatus( callback, "This selection is already being generated: " + request.selectionId );
			return;
		}

		deliverStatus( callback, "Generating " + modelSource.label + " mesh: " + request.selectionId );
		executor.submit( () -> {
			try {
				Result result = execute( request, callback );
				SwingUtilities.invokeLater( () -> callback.completed( result ) );
			} catch ( Throwable error ) {
				Failure failure = error instanceof JobFailure
						? ( (JobFailure) error ).failure
						: new Failure( usefulMessage( error ), null, request.logFile, request.resultFile );
				deliverFailure( callback, failure );
			} finally {
				running.remove( request.selectionId );
			}
		} );
	}

	private Result execute( Request request, Callback callback ) throws Exception {
		Path output = request.outputDirectory.toPath();
		Files.createDirectories( output );
		Files.createDirectories( request.logFile.getParentFile().toPath() );

		boolean publishedCacheIsReady = false;
		try {
			validateBlockDirectory( request.outputDirectory, request.selectionId,
					request.pipelineContractVersion, request.osmPrealign );
			publishedCacheIsReady = true;
		} catch ( IOException notAReadyCache ) {
			// A PLANNED/FAILED/incomplete directory is not a cache hit. The bridge
			// will quarantine any stale artefacts before attempting a fresh build.
		}
		if ( publishedCacheIsReady ) {
			// Freeze the already validated/loadable mesh first. Appearance backfill
			// is not allowed to influence this geometry decision.
			List<BuildingResult> buildings = loadBuildingResults( request );
			if ( request.modelSource == ModelSource.SATELLITE_TILES ) {
				deliverStatus( callback, "Checking cached satellite roof references: " + request.selectionId );
				try {
					backfillCachedRoofReferences( request );
				} catch ( Exception appearanceFailure ) {
					// Roof appearance is additive. A missing interpreter, missing tile, or
					// interrupted backfill must never force mesh regeneration.
					System.err.println( "Cached roof-reference backfill unavailable for "
							+ request.selectionId + ": " + usefulMessage( appearanceFailure ) );
				}
			}
			deliverStatus( callback, "Reusing complete " + request.modelSource.label
					+ " mesh: " + request.selectionId );
			return new Result( request, buildings );
		}

		writeRequestAtomically( request );

		File bridge = findBridge( request.workspace );
		File conda = new File( TweedSettings.settings.condaExecutable == null
				? "" : TweedSettings.settings.condaExecutable ).getAbsoluteFile();
		String environment = TweedSettings.settings.condaEnvironment;
		if ( !conda.isFile() )
			throw fail( "Conda executable was not found: " + conda, null, request );
		if ( environment == null || environment.trim().isEmpty() )
			throw fail( "The configured Conda environment is empty", null, request );

		deliverStatus( callback, "Preparing imagery and running Sat3DGen "
				+ request.modelSource.label + ": " + request.selectionId );
		ProcessBuilder builder = new ProcessBuilder(
				conda.getAbsolutePath(), "run", "--no-capture-output",
				"-n", environment,
				"python", "-B", bridge.getAbsolutePath(),
				"build-selection", "--request", request.requestFile.getAbsolutePath(), "--execute" );
		builder.directory( bridge.getParentFile().getParentFile() );
		builder.redirectErrorStream( true );
		builder.redirectOutput( ProcessBuilder.Redirect.to( request.logFile ) );
		builder.environment().put( "PYTHONUNBUFFERED", "1" );

		Process process;
		try {
			process = builder.start();
		} catch ( IOException error ) {
			throw fail( "Unable to start the selection bridge: " + usefulMessage( error ), null, request );
		}

		int exitCode;
		try {
			if ( !process.waitFor( request.modelSource.timeoutSeconds, TimeUnit.SECONDS ) ) {
				process.destroy();
				if ( !process.waitFor( 10, TimeUnit.SECONDS ) )
					process.destroyForcibly();
				throw fail( "The " + request.modelSource.label + " bridge exceeded its "
						+ request.modelSource.timeoutSeconds + " second timeout", null, request );
			}
			exitCode = process.exitValue();
		} catch ( InterruptedException error ) {
			Thread.currentThread().interrupt();
			process.destroy();
			throw fail( "The selection bridge was interrupted", null, request );
		}
		if ( exitCode != 0 )
			throw fail( "The selection bridge did not publish a complete mesh", exitCode, request );

		validateBlockDirectory( request.outputDirectory, request.selectionId,
				request.pipelineContractVersion, request.osmPrealign );
		deliverStatus( callback, "Preparing independent building meshes: " + request.selectionId );
		List<BuildingResult> buildings = loadBuildingResults( request );

		return new Result( request, buildings );
	}

	private void backfillCachedRoofReferences( Request request ) throws Exception {
		File bridge = findBridge( request.workspace );
		File conda = new File( TweedSettings.settings.condaExecutable == null
				? "" : TweedSettings.settings.condaExecutable ).getAbsoluteFile();
		String environment = TweedSettings.settings.condaEnvironment;
		if ( !conda.isFile() )
			throw new IOException( "Conda executable was not found: " + conda );
		if ( environment == null || environment.trim().isEmpty() )
			throw new IOException( "The configured Conda environment is empty" );

		File roofLog = new File( request.logFile.getParentFile(),
				request.selectionId + "-roof-reference.log" );
		Files.createDirectories( roofLog.getParentFile().toPath() );
		ProcessBuilder builder = new ProcessBuilder( roofBackfillCommand(
				conda, environment, bridge, request.outputDirectory ) );
		builder.directory( bridge.getParentFile().getParentFile() );
		builder.redirectErrorStream( true );
		builder.redirectOutput( ProcessBuilder.Redirect.to( roofLog ) );
		builder.environment().put( "PYTHONUNBUFFERED", "1" );
		Process process = builder.start();
		int exitCode;
		try {
			if ( !process.waitFor( ROOF_BACKFILL_TIMEOUT_SECONDS, TimeUnit.SECONDS ) ) {
				process.destroy();
				if ( !process.waitFor( 2, TimeUnit.SECONDS ) )
					process.destroyForcibly();
				throw new IOException( "Roof-reference backfill timed out after "
						+ ROOF_BACKFILL_TIMEOUT_SECONDS + " seconds; see " + roofLog );
			}
			exitCode = process.exitValue();
		} catch ( InterruptedException interrupted ) {
			Thread.currentThread().interrupt();
			process.destroyForcibly();
			throw new IOException( "Roof-reference backfill was interrupted", interrupted );
		}
		if ( exitCode != 0 )
			throw new IOException( "Roof-reference backfill exited with " + exitCode
					+ "; see " + roofLog );
	}

	static List<String> roofBackfillCommand( File conda, String environment,
			File bridge, File publication ) {
		List<String> command = new ArrayList<>();
		Collections.addAll( command,
				conda.getAbsolutePath(), "run", "--no-capture-output",
				"-n", environment,
				"python", "-B", bridge.getAbsolutePath(),
				"backfill-roof-references", "--publication", publication.getAbsolutePath() );
		return command;
	}

	private static void writeRequestAtomically( Request request ) throws IOException {
		Path directory = request.outputDirectory.toPath();
		Path temporary = Files.createTempFile( directory, "request-", ".json.tmp" );
		try {
			new ObjectMapper().writerWithDefaultPrettyPrinter().writeValue( temporary.toFile(), request.json );
			moveAtomically( temporary, request.requestFile.toPath() );
		} finally {
			Files.deleteIfExists( temporary );
		}
	}

	private static File ensureMiniMesh( File outputDirectory ) throws IOException {
		File target = new File( outputDirectory, "minimesh" );
		try {
			validateMiniMesh( target );
			return target;
		} catch ( IOException missingOrInvalid ) {
			// Rebuild into a distinct unpublished directory. A failed conversion is
			// never loaded by the GUI and does not corrupt a previous valid result.
		}

		File partial = new File( outputDirectory, "minimesh.part-" + UUID.randomUUID() );
		Matrix4d identity = new Matrix4d();
		identity.setIdentity();
		MiniTransform.convertToMini(
				Collections.singletonList( new File( outputDirectory, GISGen.CROPPED_OBJ ) ),
				partial, identity, true, new Vector3d() );
		validateMiniMesh( partial );

		if ( target.exists() ) {
			File retained = new File( outputDirectory, "minimesh.invalid-" + System.currentTimeMillis() );
			Files.move( target.toPath(), retained.toPath() );
		}
		moveAtomically( partial.toPath(), target.toPath() );
		return target;
	}

	static void validateBlockDirectory( File directory ) throws IOException {
		validateBlockDirectory( directory, null, PIPELINE_CONTRACT_VERSION, true );
	}

	static void validateBlockDirectory( File directory, String expectedSelectionId ) throws IOException {
		validateBlockDirectory( directory, expectedSelectionId, PIPELINE_CONTRACT_VERSION, true );
	}

	static void validateBlockDirectory( File directory, String expectedSelectionId,
			String expectedPipelineContract, boolean expectedOsmPrealign ) throws IOException {
		File result = new File( directory, "result.json" );
		JsonNode resultJson = readJsonObject( result, "Selection result" );
		if ( !"READY".equalsIgnoreCase( resultJson.path( "status" ).asText() ) )
			throw new IOException( "Selection result is not READY: " + result );
		String resultSelectionId = requiredText( resultJson, "selection_id", "Selection result" );
		String resultStableId = requiredText( resultJson, "stable_id", "Selection result" );
		if ( !resultSelectionId.equals( resultStableId ) ||
				( expectedSelectionId != null && !expectedSelectionId.equals( resultSelectionId ) ) )
			throw new IOException( "Selection result identity does not match request: " + result );
		if ( !expectedPipelineContract.equals( resultJson.path( "pipeline_contract_version" ).asText() ) )
			throw new IOException( "Selection result has the wrong pipeline contract: " + result );
		if ( resultJson.path( "osm_prealign" ).asBoolean( false ) != expectedOsmPrealign )
			throw new IOException( "Selection result has the wrong OSM-prealignment mode: " + result );
		if ( !BUILDING_PUBLICATION_VERSION.equals( resultJson.path( "building_publication_version" ).asText() ) )
			throw new IOException( "Selection result has no supported per-building publication: " + result );
		validateObj( new File( directory, GISGen.CROPPED_OBJ ) );
		validateObj( new File( directory, "gis.obj" ) );
		validateObj( new File( directory, "gis_footprints.obj" ) );
		validateBuildingPublication( directory, resultJson, resultSelectionId, expectedPipelineContract );
	}

	private static List<BuildingResult> loadBuildingResults( Request request ) throws IOException {
		File resultFile = new File( request.outputDirectory, "result.json" );
		JsonNode resultJson = readJsonObject( resultFile, "Selection result" );
		BuildingPublication publication = validateBuildingPublication(
				request.outputDirectory, resultJson, request.selectionId, request.pipelineContractVersion );

		Set<String> expected = new LinkedHashSet<>( request.footprintsById.keySet() );
		if ( !expected.equals( publication.allFootprintIds ) )
			throw new IOException( "Published building footprints do not exactly match the request; expected "
					+ expected + " but found " + publication.allFootprintIds );

		List<BuildingResult> results = new ArrayList<>();
		for ( PublishedBuilding published : publication.loadable ) {
			LoopL<Point3d> buildingFootprints = new LoopL<>();
			for ( String footprintId : published.footprintIds ) {
				Loop<Point3d> footprint = request.footprintsById.get( footprintId );
				if ( footprint == null )
					throw new IOException( "Published building references an unknown footprint: " + footprintId );
				buildingFootprints.add( copyLoop( footprint ) );
			}

			File miniMesh = ensureMiniMesh( published.directory );
			validateMiniMesh( miniMesh );
			VertexColorObjGen.ParsedObj vertexColorObj = null;
			try {
				// execute/loadBuildingResults runs on the service worker, so large OBJ parsing
				// never blocks Swing's event-dispatch thread.
				vertexColorObj = VertexColorObjGen.parse( published.croppedObj );
			} catch ( IOException invalidOrMissingColor ) {
				System.err.println( "Vertex-colour display unavailable for " + published.id
						+ "; using semantic BlockGen: " + invalidOrMissingColor.getMessage() );
			}
			results.add( new BuildingResult( published, miniMesh, buildingFootprints, vertexColorObj,
					request.modelSource ) );
		}
		return results;
	}

	private static BuildingPublication validateBuildingPublication( File directory, JsonNode resultJson,
			String expectedSelectionId, String expectedPipelineContract ) throws IOException {
		JsonNode resultSummary = requiredObject( resultJson, "buildings_summary", "Selection result" );
		JsonNode resultBuildings = requiredArray( resultJson, "buildings", "Selection result" );
		JsonNode outputs = requiredObject( resultJson, "outputs", "Selection result" );
		String indexValue = requiredText( outputs, "buildings_index", "Selection result outputs" );

		File root = directory.getCanonicalFile();
		File expectedIndex = new File( new File( root, "buildings" ), "index.json" ).getCanonicalFile();
		File indexFile = resolveContained( root, indexValue, "buildings index" );
		if ( !expectedIndex.equals( indexFile ) )
			throw new IOException( "Buildings index must be published at " + expectedIndex + ", not " + indexFile );

		JsonNode index = readJsonObject( indexFile, "Buildings index" );
		if ( !index.path( "schema_version" ).isIntegralNumber() || index.path( "schema_version" ).asInt() != 1 )
			throw new IOException( "Unsupported buildings index schema: " + indexFile );
		if ( !BUILDINGS_INDEX_KIND.equals( index.path( "kind" ).asText() ) )
			throw new IOException( "Unexpected buildings index kind: " + indexFile );
		if ( !BUILDING_PUBLICATION_VERSION.equals( index.path( "building_publication_version" ).asText() ) ||
				!expectedPipelineContract.equals( index.path( "pipeline_contract_version" ).asText() ) )
			throw new IOException( "Buildings index contract is not supported: " + indexFile );
		if ( !"READY".equals( index.path( "status" ).asText() ) )
			throw new IOException( "Buildings index is not READY: " + indexFile );
		if ( !expectedSelectionId.equals( requiredText( index, "selection_id", "Buildings index" ) ) ||
				!expectedSelectionId.equals( requiredText( index, "stable_id", "Buildings index" ) ) )
			throw new IOException( "Buildings index identity does not match the selection: " + indexFile );

		JsonNode indexSummary = requiredObject( index, "summary", "Buildings index" );
		JsonNode indexBuildings = requiredArray( index, "buildings", "Buildings index" );
		if ( !resultSummary.equals( indexSummary ) || !resultBuildings.equals( indexBuildings ) )
			throw new IOException( "Selection result and buildings index disagree: " + indexFile );

		Set<String> ids = new LinkedHashSet<>();
		Set<String> componentIds = new LinkedHashSet<>();
		Set<String> footprintIds = new LinkedHashSet<>();
		Set<String> directories = new LinkedHashSet<>();
		List<PublishedBuilding> loadable = new ArrayList<>();
		int readyCount = 0, coarseReadyCount = 0, rejectedCount = 0, emptyCount = 0;
		File buildingsRoot = new File( root, "buildings" ).getCanonicalFile();

		for ( int position = 0; position < indexBuildings.size(); position++ ) {
			JsonNode entry = indexBuildings.get( position );
			if ( entry == null || !entry.isObject() )
				throw new IOException( "Buildings index entry " + position + " is not an object" );
			String context = "Buildings index entry " + position;
			String id = requiredText( entry, "id", context );
			String componentId = requiredText( entry, "component_id", context );
			String footprintId = requiredText( entry, "footprint_id", context );
			if ( !ids.add( id ) || !componentIds.add( componentId ) )
				throw new IOException( "Buildings index contains a duplicate id/component_id: " + id );

			JsonNode footprintArray = requiredArray( entry, "footprint_ids", context );
			if ( footprintArray.size() != 1 || !footprintArray.get( 0 ).isTextual() )
				throw new IOException( context + " must contain exactly one textual footprint_ids entry" );
			String listedFootprintId = footprintArray.get( 0 ).asText();
			if ( !footprintId.equals( listedFootprintId ) || !footprintIds.add( footprintId ) )
				throw new IOException( context + " has a mismatched or duplicate footprint id: " + footprintId );
			List<String> entryFootprintIds = Collections.singletonList( footprintId );

			String status = requiredText( entry, "status", context );
			if ( !"READY".equals( status ) && !"COARSE_READY".equals( status ) &&
					!"REJECTED".equals( status ) && !"EMPTY".equals( status ) )
				throw new IOException( context + " has unsupported status: " + status );
			if ( "COARSE_READY".equals( status ) )
				coarseReadyCount++;
			else if ( "REJECTED".equals( status ) )
				rejectedCount++;
			else if ( "EMPTY".equals( status ) )
				emptyCount++;

			JsonNode publishableNode = entry.get( "publishable" );
			if ( publishableNode == null || !publishableNode.isBoolean() )
				throw new IOException( context + " has no boolean publishable field" );
			boolean publishable = publishableNode.asBoolean();
			boolean loadableStatus = "READY".equals( status ) || "COARSE_READY".equals( status );
			if ( publishable != loadableStatus )
				throw new IOException( context + " has inconsistent status/publishable fields" );
			if ( !requiredObject( entry, "metrics", context ).isObject() )
				throw new IOException( context + " has no metrics object" );
			if ( !publishable )
				continue;

			readyCount++;
			String relativeDirectory = requiredText( entry, "relative_dir", context );
			Path relativePath;
			try {
				relativePath = Paths.get( relativeDirectory );
			} catch ( RuntimeException invalidPath ) {
				throw new IOException( context + " has an invalid relative_dir", invalidPath );
			}
			if ( relativePath.isAbsolute() )
				throw new IOException( context + " relative_dir must not be absolute: " + relativeDirectory );
			File buildingDirectory = new File( root, relativeDirectory ).getCanonicalFile();
			ensureContained( buildingsRoot, buildingDirectory, context + " directory" );
			if ( buildingDirectory.equals( buildingsRoot ) || !directories.add( buildingDirectory.getPath() ) )
				throw new IOException( context + " has an invalid or duplicate building directory" );

			JsonNode entryOutputs = requiredObject( entry, "outputs", context );
			File cropped = resolveExpectedOutput( root, buildingDirectory,
					requiredText( entryOutputs, "cropped_obj", context + " outputs" ), GISGen.CROPPED_OBJ );
			File gis = resolveExpectedOutput( root, buildingDirectory,
					requiredText( entryOutputs, "gis_obj", context + " outputs" ), "gis.obj" );
			File gisFootprints = resolveExpectedOutput( root, buildingDirectory,
					requiredText( entryOutputs, "gis_footprints_obj", context + " outputs" ), "gis_footprints.obj" );
			validateObj( cropped );
			validateObj( gis );
			validateObj( gisFootprints );

			File metadataFile = new File( buildingDirectory, "building.json" );
			JsonNode metadata = readJsonObject( metadataFile, context + " metadata" );
			if ( !entry.equals( metadata ) )
				throw new IOException( context + " does not match its building.json metadata" );

			String sourceFeatureId = optionalText( entry, "source_feature_id", context );
			loadable.add( new PublishedBuilding( id, componentId, footprintId,
					entryFootprintIds, sourceFeatureId, status, buildingDirectory,
					cropped, gis, gisFootprints ) );
		}

		validateSummaryCount( indexSummary, "requested", indexBuildings.size(), indexFile );
		validateSummaryCount( indexSummary, "ready", readyCount, indexFile );
		validateSummaryCount( indexSummary, "coarse_ready", coarseReadyCount, indexFile );
		validateSummaryCount( indexSummary, "rejected", rejectedCount, indexFile );
		validateSummaryCount( indexSummary, "empty", emptyCount, indexFile );
		validateSummaryCount( indexSummary, "failed", 0, indexFile );
		if ( loadable.isEmpty() )
			throw new IOException( "Buildings index contains no publishable building: " + indexFile );
		return new BuildingPublication( loadable, footprintIds );
	}

	private static JsonNode readJsonObject( File file, String description ) throws IOException {
		if ( file == null || !file.isFile() || file.length() == 0 )
			throw new IOException( description + " is missing or empty: " + file );
		JsonNode value;
		try {
			value = new ObjectMapper().readTree( file );
		} catch ( IOException invalidJson ) {
			throw new IOException( description + " is not valid JSON: " + file, invalidJson );
		}
		if ( value == null || !value.isObject() )
			throw new IOException( description + " must be a JSON object: " + file );
		return value;
	}

	private static String requiredText( JsonNode parent, String field, String context ) throws IOException {
		JsonNode value = parent.get( field );
		if ( value == null || !value.isTextual() || value.asText().trim().isEmpty() )
			throw new IOException( context + " has no non-empty string field " + field );
		return value.asText();
	}

	private static String optionalText( JsonNode parent, String field, String context ) throws IOException {
		JsonNode value = parent.get( field );
		if ( value == null || value.isNull() )
			return null;
		if ( !value.isTextual() )
			throw new IOException( context + " field " + field + " must be a string or null" );
		String text = value.asText().trim();
		return text.isEmpty() ? null : text;
	}

	private static JsonNode requiredObject( JsonNode parent, String field, String context ) throws IOException {
		JsonNode value = parent.get( field );
		if ( value == null || !value.isObject() )
			throw new IOException( context + " has no object field " + field );
		return value;
	}

	private static JsonNode requiredArray( JsonNode parent, String field, String context ) throws IOException {
		JsonNode value = parent.get( field );
		if ( value == null || !value.isArray() )
			throw new IOException( context + " has no array field " + field );
		return value;
	}

	private static File resolveContained( File root, String value, String description ) throws IOException {
		File raw = new File( value );
		File resolved = ( raw.isAbsolute() ? raw : new File( root, value ) ).getCanonicalFile();
		ensureContained( root, resolved, description );
		return resolved;
	}

	private static File resolveExpectedOutput( File root, File buildingDirectory,
			String value, String expectedName ) throws IOException {
		File expected = new File( buildingDirectory, expectedName ).getCanonicalFile();
		File raw = new File( value );
		File resolved;
		if ( raw.isAbsolute() )
			resolved = raw.getCanonicalFile();
		else {
			File fromRoot = new File( root, value ).getCanonicalFile();
			File fromBuilding = new File( buildingDirectory, value ).getCanonicalFile();
			if ( expected.equals( fromRoot ) )
				resolved = fromRoot;
			else if ( expected.equals( fromBuilding ) )
				resolved = fromBuilding;
			else
				throw new IOException( "Published output does not resolve to " + expected + ": " + value );
		}
		ensureContained( root, resolved, "building output" );
		if ( !expected.equals( resolved ) )
			throw new IOException( "Published output does not match its building directory: " + resolved );
		return resolved;
	}

	private static void ensureContained( File root, File child, String description ) throws IOException {
		Path rootPath = root.getCanonicalFile().toPath();
		Path childPath = child.getCanonicalFile().toPath();
		if ( !childPath.startsWith( rootPath ) )
			throw new IOException( description + " escapes " + rootPath + ": " + childPath );
	}

	private static void validateSummaryCount( JsonNode summary, String field, int expected, File index )
			throws IOException {
		JsonNode value = summary.get( field );
		if ( value == null || !value.isIntegralNumber() || value.asInt() != expected )
			throw new IOException( "Buildings summary " + field + " does not match entries in " + index );
	}

	static void validateMiniMesh( File directory ) throws IOException {
		if ( directory == null || !directory.isDirectory() )
			throw new IOException( "MiniMesh directory is missing: " + directory );
		File index = new File( directory, MiniTransform.INDEX );
		if ( !index.isFile() || index.length() == 0 )
			throw new IOException( "MiniMesh index is missing or empty: " + index );

		boolean modelFound = false;
		try ( DirectoryStream<Path> children = Files.newDirectoryStream( directory.toPath() ) ) {
			for ( Path child : children ) {
				File model = child.resolve( MiniTransform.OBJ ).toFile();
				if ( model.isFile() ) {
					validateObj( model );
					modelFound = true;
				}
			}
		}
		if ( !modelFound )
			throw new IOException( "MiniMesh contains no model.obj tile: " + directory );
	}

	static void validateObj( File file ) throws IOException {
		if ( file == null || !file.isFile() || file.length() == 0 )
			throw new IOException( "Required OBJ is missing or empty: " + file );

		boolean vertex = false, face = false;
		try ( BufferedReader reader = Files.newBufferedReader( file.toPath(), StandardCharsets.UTF_8 ) ) {
			String line;
			while ( ( line = reader.readLine() ) != null && !( vertex && face ) ) {
				vertex |= line.startsWith( "v " );
				face |= line.startsWith( "f " );
			}
		}
		if ( !vertex || !face )
			throw new IOException( "Required OBJ has no usable vertices/faces: " + file );
	}

	private static File findBridge( File workspace ) throws IOException {
		List<File> starts = new ArrayList<>();
		starts.add( new File( System.getProperty( "user.dir" ) ) );
		starts.add( workspace );

		for ( File start : starts ) {
			File current = start == null ? null : start.getCanonicalFile();
			while ( current != null ) {
				File candidate = new File( new File( current, "bridge" ), "run.py" );
				if ( candidate.isFile() )
					return candidate.getCanonicalFile();
				if ( "bridge".equalsIgnoreCase( current.getName() ) ) {
					candidate = new File( current, "run.py" );
					if ( candidate.isFile() )
						return candidate.getCanonicalFile();
				}
				current = current.getParentFile();
			}
		}
		throw new IOException( "Unable to locate myProject bridge/run.py from user.dir or workspace ancestors" );
	}

	static LoopL<Point3d> deepCopy( LoopL<Point3d> source ) {
		if ( source == null || source.isEmpty() )
			throw new IllegalArgumentException( "The selected block has no footprints" );
		LoopL<Point3d> copy = new LoopL<>();
		Set<String> seen = Collections.newSetFromMap( new LinkedHashMap<String, Boolean>() );
		for ( Loop<Point3d> loop : source ) {
			if ( loop == null || loop.count() < 3 )
				throw new IllegalArgumentException( "A selected footprint has fewer than three points" );
			// GIS block construction can return the same Loop twice when duplicate
			// source features share identical geometry. Treat coincident geometry as
			// one footprint so the bridge receives unique, stable footprint IDs.
			if ( !seen.add( canonicalLoop( loop ) ) )
				continue;
			copy.add( copyLoop( loop ) );
		}
		if ( copy.isEmpty() )
			throw new IllegalArgumentException( "The selected block has no unique footprints" );
		return copy;
	}

	@SuppressWarnings( { "rawtypes", "unchecked" } )
	private static Loop<Point3d> copyLoop( Loop<Point3d> source ) {
		Loop<Point3d> target;
		if ( source instanceof SuperLoop ) {
			SuperLoop original = (SuperLoop) source;
			SuperLoop<Point3d> copied = new SuperLoop<>( (String) original.properties.get( "name" ) );
			copied.properties = new HashMap<>( original.properties );
			target = copied;
		} else
			target = new Loop<>();

		for ( Point3d point : source )
			target.append( new Point3d( point ) );
		for ( Loop<Point3d> hole : source.holes )
			target.holes.add( copyLoop( hole ) );
		return target;
	}

	static String stableSelectionId( LoopL<Point3d> footprints ) {
		Set<String> unique = Collections.newSetFromMap( new LinkedHashMap<String, Boolean>() );
		for ( Loop<Point3d> loop : footprints )
			unique.add( canonicalLoop( loop ) );
		List<String> loops = new ArrayList<>( unique );
		Collections.sort( loops );
		return sha256( String.join( "|", loops ) ).substring( 0, 20 );
	}

	private static String canonicalLoop( Loop<Point3d> loop ) {
		List<String> points = new ArrayList<>();
		for ( Point3d point : loop )
			points.add( String.format( Locale.ROOT, "%.6f,%.6f", point.x, point.z ) );
		if ( points.isEmpty() )
			throw new IllegalArgumentException( "Cannot canonicalise an empty footprint" );

		List<String> candidates = new ArrayList<>();
		for ( int direction : new int[] { 1, -1 } )
			for ( int start = 0; start < points.size(); start++ ) {
				StringBuilder candidate = new StringBuilder();
				for ( int offset = 0; offset < points.size(); offset++ ) {
					if ( offset > 0 )
						candidate.append( ';' );
					int index = ( start + direction * offset ) % points.size();
					if ( index < 0 )
						index += points.size();
					candidate.append( points.get( index ) );
				}
				candidates.add( candidate.toString() );
			}
		String outer = Collections.min( candidates, Comparator.naturalOrder() );
		if ( loop.holes.isEmpty() )
			return outer;
		List<String> holes = new ArrayList<>();
		for ( Loop<Point3d> hole : loop.holes )
			holes.add( canonicalLoop( hole ) );
		Collections.sort( holes );
		return outer + "[" + String.join( "|", holes ) + "]";
	}

	private static String sha256( String value ) {
		try {
			MessageDigest digest = MessageDigest.getInstance( "SHA-256" );
			byte[] bytes = digest.digest( value.getBytes( StandardCharsets.UTF_8 ) );
			StringBuilder out = new StringBuilder();
			for ( byte b : bytes )
				out.append( String.format( Locale.ROOT, "%02x", b & 0xff ) );
			return out.toString();
		} catch ( NoSuchAlgorithmException impossible ) {
			throw new IllegalStateException( impossible );
		}
	}

	private static void moveAtomically( Path source, Path target ) throws IOException {
		try {
			Files.move( source, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING );
		} catch ( AtomicMoveNotSupportedException unsupported ) {
			Files.move( source, target, StandardCopyOption.REPLACE_EXISTING );
		}
	}

	private static JobFailure fail( String reason, Integer exitCode, Request request ) {
		return new JobFailure( new Failure( reason, exitCode, request.logFile, request.resultFile ) );
	}

	private static final class JobFailure extends Exception {
		private static final long serialVersionUID = 1L;
		final Failure failure;
		JobFailure( Failure failure ) {
			super( failure.reason );
			this.failure = failure;
		}
	}

	private static void deliverStatus( Callback callback, String status ) {
		SwingUtilities.invokeLater( () -> callback.statusChanged( status ) );
	}

	private static void deliverFailure( Callback callback, Failure failure ) {
		SwingUtilities.invokeLater( () -> callback.failed( failure ) );
	}

	private static String usefulMessage( Throwable error ) {
		String message = error.getMessage();
		return error.getClass().getSimpleName() + ( message == null ? "" : ": " + message );
	}

	private static void appendFileDetails( StringBuilder out, String label, File file, int limit ) {
		if ( file == null )
			return;
		out.append( '\n' ).append( label ).append( ": " ).append( file.getAbsolutePath() );
		if ( !file.isFile() ) {
			out.append( " (not written)" );
			return;
		}
		try {
			byte[] data = Files.readAllBytes( file.toPath() );
			String content = new String( data, StandardCharsets.UTF_8 );
			if ( content.length() > limit )
				content = "..." + content.substring( content.length() - limit );
			if ( !content.trim().isEmpty() )
				out.append( "\n" ).append( content.trim() );
		} catch ( IOException error ) {
			out.append( " (unable to read: " ).append( error.getMessage() ).append( ')' );
		}
	}
}
