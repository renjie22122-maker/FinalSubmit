package org.twak.tweed.gen;

import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.imageio.ImageIO;
import javax.swing.SwingUtilities;
import javax.vecmath.Point3d;

import org.twak.tweed.Tweed;
import org.twak.tweed.TweedSettings;
import org.twak.utils.collections.Loop;
import org.twak.utils.collections.LoopL;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Acquires Street View panoramas for one selected {@link BlockGen}.  The
 * request is made only from that block's local-metre footprints; no legacy
 * data_builder panorama or label directory participates in this workflow.
 */
final class SelectedBlockPanoramaService {

	private static final ObjectMapper MAPPER = new ObjectMapper();
	private static final String REQUEST_KIND = "myProject.block_panoramas.request";
	private static final String PLAN_SCHEMA = "myProject.block_panoramas.plan-v1";
	private static final String IMPORT_SCHEMA = "chordatlas-static-pano-report-v1";
	private static final String PROMOTION_SCHEMA = "myProject.block_panoramas.promotion-v1";
	static final long PLAN_TIMEOUT_SECONDS = 15 * 60;
	static final long SAMPLE_TIMEOUT_SECONDS = 10 * 60;
	static final long BATCH_TIMEOUT_SECONDS = 90 * 60;
	static final long PROMOTION_TIMEOUT_SECONDS = 2 * 60;
	private static final long PROCESS_STOP_GRACE_SECONDS = 3;

	interface Callback {
		void statusChanged( String status );
		void sampleReady( Sample sample, Approval approval );
		void completed( Result result );
		void cancelled( Sample sample );
		void failed( Failure failure );
	}

	interface Approval {
		void approve();
		void cancel();
	}

	static final class Sample {
		final File image;
		final BufferedImage preview;
		final File report;
		final File log;

		Sample( File image, BufferedImage preview, File report, File log ) {
			this.image = image;
			this.preview = preview;
			this.report = report;
			this.log = log;
		}
	}

	static final class Result {
		final File panoramaFolder;
		final File report;
		final File log;
		final int succeeded;
		final int existing;

		Result( Request request, JsonNode report ) {
			this.panoramaFolder = request.panoramaFolder;
			this.report = request.batchReport;
			this.log = request.batchLog;
			this.succeeded = report.path( "summary" ).path( "succeeded" ).asInt();
			this.existing = report.path( "summary" ).path( "existing" ).asInt();
		}
	}

	static final class Failure {
		final String reason;
		final Integer exitCode;
		final File log;
		final File report;

		Failure( String reason, Integer exitCode, File log, File report ) {
			this.reason = reason;
			this.exitCode = exitCode;
			this.log = log;
			this.report = report;
		}

		String details() {
			StringBuilder out = new StringBuilder( reason == null ? "unknown failure" : reason );
			if ( exitCode != null )
				out.append( "\nExit code: " ).append( exitCode );
			if ( report != null )
				out.append( "\nReport: " ).append( report );
			if ( log != null )
				out.append( "\nLog: " ).append( log );
			return out.toString();
		}
	}

	static final class Request {
		final String selectionId;
		final File workspace;
		final File referenceDirectory;
		final File requestFile;
		final File planReport, sampleReport, batchReport, promotionReport;
		final File planLog, sampleLog, batchLog, promotionLog;
		final File panoramaFolder, todoFile, liveTodoFile;
		final Map<String, Object> json;

		Request( File workspace, File blockRoot, LoopL<Point3d> footprints ) throws IOException {
			this.workspace = workspace.getCanonicalFile();
			File canonicalRoot = blockRoot.getCanonicalFile();
			if ( !canonicalRoot.toPath().startsWith( this.workspace.toPath() ) )
				throw new IOException( "Selected block is outside the current workspace: " + canonicalRoot );
			if ( footprints == null || footprints.isEmpty() )
				throw new IOException( "The selected block has no footprints" );

			selectionId = SelectedBlockMeshService.stableSelectionId( footprints );
			referenceDirectory = new File( new File( canonicalRoot, "references" ), "panoramas" )
					.getCanonicalFile();
			if ( !referenceDirectory.toPath().startsWith( this.workspace.toPath() ) )
				throw new IOException( "Panorama reference directory escapes the workspace" );
			requestFile = new File( referenceDirectory, "request.json" );
			planReport = new File( referenceDirectory, "plan_report.json" );
			sampleReport = new File( referenceDirectory, "sample_report.json" );
			batchReport = new File( referenceDirectory, "batch_report.json" );
			promotionReport = new File( referenceDirectory, "promotion_report.json" );
			planLog = new File( referenceDirectory, "plan.log" );
			sampleLog = new File( referenceDirectory, "sample.log" );
			batchLog = new File( referenceDirectory, "batch.log" );
			promotionLog = new File( referenceDirectory, "promotion.log" );
			panoramaFolder = new File( this.workspace, "panos" ).getCanonicalFile();
			if ( !panoramaFolder.toPath().startsWith( this.workspace.toPath() ) )
				throw new IOException( "Panorama output directory escapes the workspace" );
			// The planner/sample/batch own this selection-scoped file.  The live
			// ChordAtlas todo.list is not touched until an approved batch succeeds.
			todoFile = new File( referenceDirectory, "todo.list" );
			liveTodoFile = new File( panoramaFolder, "todo.list" );

			List<Map<String, Object>> footprintJson = new ArrayList<>();
			int index = 1;
			for ( Loop<Point3d> footprint : footprints ) {
				List<List<Double>> points = new ArrayList<>();
				for ( Point3d point : footprint ) {
					List<Double> pair = new ArrayList<>( 2 );
					pair.add( point.x );
					pair.add( point.z );
					points.add( pair );
				}
				if ( points.size() < 3 )
					throw new IOException( "A selected footprint has fewer than three vertices" );
				Map<String, Object> item = new LinkedHashMap<>();
				item.put( "id", String.format( "footprint-%03d", index++ ) );
				item.put( "points", points );
				footprintJson.add( item );
			}

			json = new LinkedHashMap<>();
			json.put( "schema_version", 1 );
			json.put( "kind", REQUEST_KIND );
			json.put( "workspace", this.workspace.getAbsolutePath() );
			json.put( "selection_id", selectionId );
			json.put( "footprints", footprintJson );
		}
	}

	/* todo.list is a workspace-wide hand-off, so only one block may own it from
	 * planning through the user's sample decision and the final batch. */
	private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor( runnable -> {
		Thread thread = new Thread( runnable, "selected-block-panoramas" );
		thread.setDaemon( true );
		return thread;
	} );
	private static final AtomicBoolean ACQUISITION_IN_USE = new AtomicBoolean();

	void submit( File blockRoot, LoopL<Point3d> footprints, Callback callback ) {
		final Request request;
		try {
			request = new Request( new File( Tweed.DATA ), blockRoot,
					SelectedBlockMeshService.deepCopy( footprints ) );
		} catch ( Throwable error ) {
			deliverFailure( callback, new Failure( usefulMessage( error ), null, null, null ) );
			return;
		}
		if ( !ACQUISITION_IN_USE.compareAndSet( false, true ) ) {
			deliverFailure( callback, new Failure(
					"Another selected-block panorama acquisition is already running or awaiting sample approval.",
					null, null, null ) );
			return;
		}

		EXECUTOR.submit( () -> prepareSample( request, callback ) );
	}

	private void prepareSample( Request request, Callback callback ) {
		File failureLog = request.planLog, failureReport = request.planReport;
		try {
			if ( System.getenv( "GOOGLE_MAPS_API_KEY" ) == null ||
					System.getenv( "GOOGLE_MAPS_API_KEY" ).trim().isEmpty() )
				throw new IOException( "GOOGLE_MAPS_API_KEY is not set in the GUI process environment" );
			Files.createDirectories( request.referenceDirectory.toPath() );
			Files.createDirectories( request.panoramaFolder.toPath() );
			writeRequestAtomically( request );

			RuntimeConfig runtime = runtimeConfig( request.workspace );
			deliverStatus( callback, "Finding Street View cameras around the selected block..." );
			runChecked( planCommand( runtime.conda, runtime.environment, runtime.bridge,
					request.requestFile, request.todoFile, request.planReport ), runtime.root,
					request.planLog, request.planReport, PLAN_TIMEOUT_SECONDS,
					"Street View camera planning" );
			validatePlanReport( request );

			deliverStatus( callback, "Downloading one Street View panorama for approval..." );
			failureLog = request.sampleLog;
			failureReport = request.sampleReport;
			runChecked( sampleCommand( runtime.conda, runtime.environment, runtime.bridge,
					request.todoFile, request.panoramaFolder, request.sampleReport ),
					runtime.root, request.sampleLog, request.sampleReport, SAMPLE_TIMEOUT_SECONDS,
					"One-panorama sample" );
			File sampleImage = validateImportReport( request.sampleReport, "sample",
					request.panoramaFolder, true );
			BufferedImage preview = ImageIO.read( sampleImage );
			if ( preview == null )
				throw new IOException( "The sample report points to an unreadable image: " + sampleImage );
			Sample sample = new Sample( sampleImage, preview, request.sampleReport, request.sampleLog );
			deliverSample( callback, sample, new ApprovalImpl( request, callback, sample ) );
		} catch ( Throwable error ) {
			ACQUISITION_IN_USE.set( false );
			deliverFailure( callback, failure( error, failureLog, failureReport ) );
		}
	}

	private final class ApprovalImpl implements Approval {
		private final Request request;
		private final Callback callback;
		private final Sample sample;
		private final AtomicBoolean decided = new AtomicBoolean();

		ApprovalImpl( Request request, Callback callback, Sample sample ) {
			this.request = request;
			this.callback = callback;
			this.sample = sample;
		}

		@Override public void approve() {
			if ( decided.compareAndSet( false, true ) )
				EXECUTOR.submit( () -> runBatch( request, callback ) );
		}

		@Override public void cancel() {
			if ( decided.compareAndSet( false, true ) ) {
				ACQUISITION_IN_USE.set( false );
				deliverCancelled( callback, sample );
			}
		}
	}

	private void runBatch( Request request, Callback callback ) {
		File failureLog = request.batchLog, failureReport = request.batchReport;
		try {
			RuntimeConfig runtime = runtimeConfig( request.workspace );
			deliverStatus( callback, "Sample approved; downloading the remaining selected-block panoramas..." );
			runChecked( batchCommand( runtime.conda, runtime.environment, runtime.bridge,
					request.todoFile, request.panoramaFolder, request.batchReport,
					request.sampleReport ), runtime.root, request.batchLog, request.batchReport,
					BATCH_TIMEOUT_SECONDS, "Street View panorama batch" );
			JsonNode report = readImportReport( request.batchReport, "batch", request.panoramaFolder );

			deliverStatus( callback, "Publishing the approved panorama plan to ChordAtlas..." );
			failureLog = request.promotionLog;
			failureReport = request.promotionReport;
			runChecked( promotionCommand( runtime.conda, runtime.environment, runtime.bridge,
					request.requestFile, request.todoFile, request.planReport, request.batchReport,
					request.promotionReport ), runtime.root,
					request.promotionLog, request.promotionReport, PROMOTION_TIMEOUT_SECONDS,
					"Panorama plan promotion" );
			validatePromotionReport( request );
			deliverCompleted( callback, new Result( request, report ) );
		} catch ( Throwable error ) {
			deliverFailure( callback, failure( error, failureLog, failureReport ) );
		} finally {
			ACQUISITION_IN_USE.set( false );
		}
	}

	static void runChecked( List<String> command, File directory, File log, File report,
			long timeoutSeconds, String stage ) throws IOException, InterruptedException {
		Files.createDirectories( log.getParentFile().toPath() );
		ProcessBuilder builder = new ProcessBuilder( command );
		builder.directory( directory );
		builder.redirectErrorStream( true );
		builder.redirectOutput( ProcessBuilder.Redirect.to( log ) );
		builder.environment().put( "PYTHONUNBUFFERED", "1" );
		Process process = builder.start();
		try {
			if ( !process.waitFor( timeoutSeconds, TimeUnit.SECONDS ) ) {
				stopProcess( process );
				throw new JobFailure( stage + " timed out after " + timeoutSeconds + " seconds",
						null, log, report );
			}
			int exitCode = process.exitValue();
			if ( exitCode != 0 )
				throw new JobFailure( stage + " failed", exitCode, log, report );
		} catch ( InterruptedException interrupted ) {
			process.destroy();
			process.destroyForcibly();
			Thread.currentThread().interrupt();
			throw interrupted;
		}
	}

	private static void stopProcess( Process process ) throws InterruptedException {
		process.destroy();
		if ( !process.waitFor( PROCESS_STOP_GRACE_SECONDS, TimeUnit.SECONDS ) ) {
			process.destroyForcibly();
			process.waitFor( PROCESS_STOP_GRACE_SECONDS, TimeUnit.SECONDS );
		}
	}

	static List<String> planCommand( File conda, String environment, File bridge,
			File request, File todo, File report ) {
		List<String> command = baseCommand( conda, environment, bridge );
		Collections.addAll( command, "prepare-block-panos", "--request", request.getAbsolutePath(),
				"--todo", todo.getAbsolutePath(),
				"--report", report.getAbsolutePath() );
		return command;
	}

	static List<String> sampleCommand( File conda, String environment, File bridge,
			File todo, File output, File report ) {
		List<String> command = baseCommand( conda, environment, bridge );
		Collections.addAll( command, "import-streetview-panos", "--todo", todo.getAbsolutePath(),
				"--output", output.getAbsolutePath(), "--report", report.getAbsolutePath(),
				"--coordinate-mode", "myproject-local" );
		return command;
	}

	static List<String> batchCommand( File conda, String environment, File bridge,
			File todo, File output, File report, File sampleReport ) {
		List<String> command = sampleCommand( conda, environment, bridge, todo, output, report );
		Collections.addAll( command, "--all", "--sample-approved", "--sample-report",
				sampleReport.getAbsolutePath() );
		return command;
	}

	static List<String> promotionCommand( File conda, String environment, File bridge,
			File request, File todo, File planReport, File batchReport, File report ) {
		List<String> command = baseCommand( conda, environment, bridge );
		Collections.addAll( command, "promote-block-panos", "--request", request.getAbsolutePath(),
				"--todo", todo.getAbsolutePath(), "--plan-report", planReport.getAbsolutePath(),
				"--batch-report", batchReport.getAbsolutePath(), "--report", report.getAbsolutePath() );
		return command;
	}

	private static List<String> baseCommand( File conda, String environment, File bridge ) {
		List<String> command = new ArrayList<>();
		Collections.addAll( command, conda.getAbsolutePath(), "run", "--no-capture-output",
				"-n", environment, "python", "-B", bridge.getAbsolutePath() );
		return command;
	}

	private static void validatePlanReport( Request request ) throws IOException {
		JsonNode report = readObject( request.planReport );
		if ( !PLAN_SCHEMA.equals( report.path( "schema" ).asText() ) ||
				!"READY".equalsIgnoreCase( report.path( "status" ).asText() ) )
			throw new IOException( "Panorama plan report is not READY or has the wrong schema" );
		if ( !request.selectionId.equals( report.path( "selection_id" ).asText() ) )
			throw new IOException( "Panorama plan report belongs to another selected block" );
		File reportedTodo = new File( report.path( "todo" ).path( "path" ).asText() ).getCanonicalFile();
		if ( !reportedTodo.equals( request.todoFile.getCanonicalFile() ) || !reportedTodo.isFile() )
			throw new IOException( "Panorama plan did not publish the expected selection-scoped todo.list" );
	}

	private static void validatePromotionReport( Request request ) throws IOException {
		JsonNode report = readObject( request.promotionReport );
		String status = report.path( "status" ).asText();
		if ( !PROMOTION_SCHEMA.equals( report.path( "schema" ).asText() ) ||
				!( "PROMOTED".equalsIgnoreCase( status ) || "UNCHANGED".equalsIgnoreCase( status ) ) )
			throw new IOException( "Panorama promotion report is not PROMOTED/UNCHANGED or has the wrong schema" );
		if ( !request.selectionId.equals( report.path( "selection_id" ).asText() ) )
			throw new IOException( "Panorama promotion report belongs to another selected block" );
		File live = new File( report.path( "live_todo" ).path( "path" ).asText() ).getCanonicalFile();
		if ( !live.equals( request.liveTodoFile.getCanonicalFile() ) || !live.isFile() )
			throw new IOException( "Approved panorama plan was not published as workspace/panos/todo.list" );
		String liveSha = report.path( "live_todo" ).path( "sha256" ).asText();
		String scopedSha = report.path( "scoped_todo" ).path( "sha256" ).asText();
		if ( liveSha.isEmpty() || !liveSha.equals( scopedSha ) || !liveSha.equals( sha256( live ) ) )
			throw new IOException( "Published workspace todo.list does not match the approved scoped plan" );
	}

	private static String sha256( File file ) throws IOException {
		try {
			MessageDigest digest = MessageDigest.getInstance( "SHA-256" );
			byte[] buffer = new byte[ 8192 ];
			try ( InputStream stream = Files.newInputStream( file.toPath() ) ) {
				for ( int count; ( count = stream.read( buffer ) ) >= 0; )
					if ( count > 0 )
						digest.update( buffer, 0, count );
			}
			StringBuilder out = new StringBuilder( 64 );
			for ( byte value : digest.digest() )
				out.append( String.format( "%02x", value & 0xff ) );
			return out.toString();
		} catch ( NoSuchAlgorithmException impossible ) {
			throw new IOException( "SHA-256 is unavailable", impossible );
		}
	}

	private static File validateImportReport( File path, String mode, File output,
			boolean requireOne ) throws IOException {
		JsonNode report = readImportReport( path, mode, output );
		JsonNode items = report.path( "items" );
		File image = null;
		int valid = 0;
		for ( JsonNode item : items )
			if ( "succeeded".equals( item.path( "status" ).asText() ) ||
					"existing".equals( item.path( "status" ).asText() ) ) {
				File candidate = new File( item.path( "output" ).asText() ).getCanonicalFile();
				if ( !candidate.getParentFile().equals( output.getCanonicalFile() ) || !candidate.isFile() )
					throw new IOException( "Panorama report output is outside workspace/panos" );
				image = candidate;
				valid++;
			}
		if ( requireOne && valid != 1 )
			throw new IOException( "Sample report must contain exactly one valid panorama" );
		return image;
	}

	private static JsonNode readImportReport( File path, String mode, File output ) throws IOException {
		JsonNode report = readObject( path );
		if ( !IMPORT_SCHEMA.equals( report.path( "schema" ).asText() ) ||
				!mode.equals( report.path( "mode" ).asText() ) || report.path( "dry_run" ).asBoolean() )
			throw new IOException( "Panorama import report has the wrong schema or mode" );
		if ( report.path( "summary" ).path( "failed" ).asInt( 1 ) != 0 )
			throw new IOException( "Panorama import report contains failed records" );
		File reportedOutput = new File( report.path( "output_dir" ).asText() ).getCanonicalFile();
		if ( !reportedOutput.equals( output.getCanonicalFile() ) )
			throw new IOException( "Panorama import report belongs to another output folder" );
		return report;
	}

	private static JsonNode readObject( File file ) throws IOException {
		if ( !file.isFile() )
			throw new IOException( "Expected report was not written: " + file );
		JsonNode node = MAPPER.readTree( file );
		if ( node == null || !node.isObject() )
			throw new IOException( "Expected a JSON object report: " + file );
		return node;
	}

	static void writeRequestAtomically( Request request ) throws IOException {
		Files.createDirectories( request.referenceDirectory.toPath() );
		Path temp = Files.createTempFile( request.referenceDirectory.toPath(), "request-", ".json.tmp" );
		try {
			MAPPER.writerWithDefaultPrettyPrinter().writeValue( temp.toFile(), request.json );
			try {
				Files.move( temp, request.requestFile.toPath(), StandardCopyOption.REPLACE_EXISTING,
						StandardCopyOption.ATOMIC_MOVE );
			} catch ( AtomicMoveNotSupportedException unsupported ) {
				Files.move( temp, request.requestFile.toPath(), StandardCopyOption.REPLACE_EXISTING );
			}
		} finally {
			Files.deleteIfExists( temp );
		}
	}

	private static final class RuntimeConfig {
		final File conda, bridge, root;
		final String environment;
		RuntimeConfig( File conda, String environment, File bridge ) {
			this.conda = conda;
			this.environment = environment;
			this.bridge = bridge;
			this.root = bridge.getParentFile().getParentFile();
		}
	}

	private static RuntimeConfig runtimeConfig( File workspace ) throws IOException {
		File conda = new File( TweedSettings.settings.condaExecutable == null ? "" :
				TweedSettings.settings.condaExecutable ).getAbsoluteFile();
		String environment = TweedSettings.settings.condaEnvironment;
		if ( !conda.isFile() )
			throw new IOException( "Conda executable was not found: " + conda );
		if ( environment == null || environment.trim().isEmpty() )
			throw new IOException( "The configured Conda environment is empty" );
		return new RuntimeConfig( conda, environment, findBridge( workspace ) );
	}

	private static File findBridge( File workspace ) throws IOException {
		File current = new File( System.getProperty( "user.dir", "." ) ).getCanonicalFile();
		for ( int depth = 0; current != null && depth < 10; depth++, current = current.getParentFile() ) {
			File candidate = new File( new File( current, "bridge" ), "run.py" );
			if ( candidate.isFile() )
				return candidate.getCanonicalFile();
		}
		current = workspace.getCanonicalFile();
		for ( int depth = 0; current != null && depth < 10; depth++, current = current.getParentFile() ) {
			File candidate = new File( new File( current, "bridge" ), "run.py" );
			if ( candidate.isFile() )
				return candidate.getCanonicalFile();
		}
		throw new IOException( "Unable to locate myProject bridge/run.py" );
	}

	private static Failure failure( Throwable error, File log, File report ) {
		if ( error instanceof JobFailure ) {
			JobFailure job = (JobFailure) error;
			return new Failure( job.getMessage(), job.exitCode, job.log, job.report );
		}
		return new Failure( usefulMessage( error ), null, log, report );
	}

	private static final class JobFailure extends IOException {
		final Integer exitCode;
		final File log, report;
		JobFailure( String message, Integer exitCode, File log, File report ) {
			super( message );
			this.exitCode = exitCode;
			this.log = log;
			this.report = report;
		}
	}

	private static String usefulMessage( Throwable error ) {
		if ( error == null ) return "unknown failure";
		String message = error.getMessage();
		return message == null || message.trim().isEmpty() ? error.getClass().getSimpleName() : message;
	}

	private static void deliverStatus( Callback callback, String value ) {
		SwingUtilities.invokeLater( () -> callback.statusChanged( value ) );
	}
	private static void deliverSample( Callback callback, Sample sample, Approval approval ) {
		SwingUtilities.invokeLater( () -> callback.sampleReady( sample, approval ) );
	}
	private static void deliverCompleted( Callback callback, Result result ) {
		SwingUtilities.invokeLater( () -> callback.completed( result ) );
	}
	private static void deliverCancelled( Callback callback, Sample sample ) {
		SwingUtilities.invokeLater( () -> callback.cancelled( sample ) );
	}
	private static void deliverFailure( Callback callback, Failure failure ) {
		SwingUtilities.invokeLater( () -> callback.failed( failure ) );
	}
}
